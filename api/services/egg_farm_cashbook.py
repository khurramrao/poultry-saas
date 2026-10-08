"""Farm Egg cashbook: live source receipts + admin-entered cash movements.

This does not create duplicate payment entries and does not alter farm COGS.
The balances cover what has been entered, not unrecorded costs or cash held by staff.
"""
from decimal import Decimal
from api.models.eggs import FarmEggCashMovement
from api.services.egg_farm_receipts import get_farm_egg_receipts

ZERO = Decimal("0.00")


def _line(day, key, reference, description, account, inflow=ZERO, outflow=ZERO, batch="", manual_id=None, source=""):
    return {
        "date": day, "key": key, "reference": reference,
        "description": description, "account": account, "inflow": inflow,
        "outflow": outflow, "batch": batch,
        "manual_id": manual_id, "source": source,
    }


def get_farm_egg_cashbook(batches, max_rows=50):
    """Compute balances from all recorded, non-void source transactions.

    batches must include CLOSED layer batches so historic receipts are not lost
    when a layer batch closes. Each source payment is counted exactly once.
    """
    receipts = get_farm_egg_receipts(batches, limit=None)
    rows = []
    for receipt in receipts["rows"]:
        if receipt["channel"] not in {"cash", "bank_wallet"}:
            continue  # Do not assume 'other' payment is physically in cash.
        rows.append(_line(
            receipt["date"], receipt["source_key"], receipt["reference"],
            f'{receipt["source"]}: {receipt["detail"]}',
            "cash" if receipt["channel"] == "cash" else "bank",
            inflow=receipt["amount"], batch=receipt["batch"],
            source="automatic",
        ))

    # An expense paid FROM Egg Management money is one transaction with two
    # effects: production expense in its original table and farm cash outflow.
    # Read original rows, never create a second COGS or manual cashbook entry.
    # Old entries default to "outside" so the rollout never retroactively
    # deducts payments the farm may have recorded separately.
    from api.models.investors import FeedEntry, MedicineEntry
    from api.models.sales import Expense
    batch_ids = [batch.pk for batch in batches]
    expense_sources = (
        (FeedEntry, "entry_date", "FEED", "Feed purchase", "notes"),
        (MedicineEntry, "entry_date", "MED", "Medicine / vaccine", "medicine_name"),
        (Expense, "expense_date", "EXP", "Farm expense", "description"),
    )
    for model, date_field, prefix, label, detail_field in expense_sources:
        for expense in model.objects.filter(
            batch_id__in=batch_ids, payment_source__in=("farm_cash", "farm_bank"),
        ).select_related("batch"):
            date_value = getattr(expense, date_field)
            account = "cash" if expense.payment_source == "farm_cash" else "bank"
            detail = (getattr(expense, detail_field, "") or "").strip()
            rows.append(_line(
                date_value, f"cost:{prefix}:{expense.pk}", f"{prefix}-{expense.pk}",
                f"{label} · {detail[:90]}" if detail else label,
                account, outflow=expense.amount,
                batch=expense.batch.batch_number, source="farm_expense",
            ))

    for movement in FarmEggCashMovement.objects.filter(is_voided=False).select_related("batch"):
        account = movement.account
        ref = movement.reference or f"FARM-CASH-{movement.id}"
        desc = movement.get_movement_type_display()
        if movement.notes:
            desc += f" · {movement.notes}"
        common = (movement.movement_date, f"manual:{movement.id}", ref, desc)
        batch = movement.batch.batch_number if movement.batch_id else ""
        kw = {"batch": batch, "manual_id": movement.pk, "source": "manual"}
        amount = movement.amount
        if movement.movement_type == "cash_to_bank":
            rows.append(_line(*common, "cash", outflow=amount, **kw))
            rows.append(_line(*common, "bank", inflow=amount, **kw))
        elif movement.movement_type == "bank_to_cash":
            rows.append(_line(*common, "bank", outflow=amount, **kw))
            rows.append(_line(*common, "cash", inflow=amount, **kw))
        elif movement.movement_type in {"opening", "capital_in", "other_in"}:
            rows.append(_line(*common, account, inflow=amount, **kw))
        else:
            rows.append(_line(*common, account, outflow=amount, **kw))

    # Stable chronological order. When source rows have only a date, order is
    # necessarily date-granular; the final account balances are exact.
    rows.sort(key=lambda r: (r["date"], r["key"], r["account"]))
    balance = {"cash": ZERO, "bank": ZERO}
    receipts_total = {"cash": ZERO, "bank": ZERO}
    outflows_total = {"cash": ZERO, "bank": ZERO}
    for row in rows:
        acct = row["account"]
        balance[acct] += row["inflow"] - row["outflow"]
        receipts_total[acct] += row["inflow"]
        outflows_total[acct] += row["outflow"]
        row["balance"] = balance[acct]
    return {
        "cash_balance": balance["cash"],
        "bank_balance": balance["bank"],
        "total_balance": balance["cash"] + balance["bank"],
        "cash_in": receipts_total["cash"],
        "cash_out": outflows_total["cash"],
        "bank_in": receipts_total["bank"],
        "bank_out": outflows_total["bank"],
        "pos_receipts": receipts["pos_receipts"],
        "direct_receipts": receipts["direct_receipts"],
        "unclassified_receipts": receipts["unclassified_receipts"],
        "rows": list(reversed(rows))[:max_rows] if max_rows is not None else list(reversed(rows)),
        "transaction_count": len(rows),
        "has_negative_cash": balance["cash"] < ZERO,
        "has_negative_bank": balance["bank"] < ZERO,
    }
