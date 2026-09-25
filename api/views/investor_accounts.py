from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from api.models.investors import (
    FeedEntry,
    InvestorAccountPayment,
    InvestorAllocation,
    InvestorSalePayout,
    FarmAccountPayment,
    FarmSaleWithdrawal,
    MedicineEntry,
)
from api.models.sales import ChickCostEntry, Expense, SaleRecord
from api.models.goats import Goat, GoatCostAllocation
from api.models.eggs import EggSale
from api.models.sensor import Batch


ZERO = Decimal("0.00")
MONEY = Decimal("0.01")
ACCOUNT_TOLERANCE = Decimal("0.50")


def _money(value):
    if value is None:
        return ZERO
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def _is_admin(user):
    return user.is_superuser or user.is_staff


def _investor_name(allocation):
    user = allocation.investor.user
    return user.get_full_name().strip() or user.username


def _share_ratio(allocation):
    starting_birds = int(allocation.batch.bird_count_initial or 0)
    if starting_birds <= 0:
        return ZERO
    return (
        Decimal(allocation.birds_owned)
        / Decimal(starting_birds)
    )


def _batch_cost_totals(batch):
    chick_cost = (
        ChickCostEntry.objects.filter(batch=batch)
        .aggregate(total=Sum("chick_cost"))["total"]
        or ZERO
    )
    carriage_cost = (
        ChickCostEntry.objects.filter(batch=batch)
        .aggregate(total=Sum("carriage_cost"))["total"]
        or ZERO
    )
    feed_cost = (
        FeedEntry.objects.filter(batch=batch)
        .aggregate(total=Sum("amount"))["total"]
        or ZERO
    )
    medicine_cost = (
        MedicineEntry.objects.filter(batch=batch)
        .aggregate(total=Sum("amount"))["total"]
        or ZERO
    )
    expense_cost = (
        Expense.objects.filter(batch=batch)
        .aggregate(total=Sum("amount"))["total"]
        or ZERO
    )

    totals = {
        "chick_cost": _money(chick_cost),
        "carriage_cost": _money(carriage_cost),
        "feed_cost": _money(feed_cost),
        "medicine_cost": _money(medicine_cost),
        "expense_cost": _money(expense_cost),
    }
    totals["total_cost"] = _money(sum(totals.values(), ZERO))
    return totals





def _farm_share_ratio(batch):
    """Residual Admin/Farm ownership after investor starting-bird allocations."""
    starting_birds = int(batch.bird_count_initial or 0)
    if starting_birds <= 0:
        return ZERO

    allocated_birds = (
        InvestorAllocation.objects.filter(batch=batch)
        .aggregate(total=Sum("birds_owned"))["total"]
        or 0
    )
    farm_birds = max(starting_birds - int(allocated_birds), 0)
    return Decimal(farm_birds) / Decimal(starting_birds)


def _farm_sale_proceeds_snapshot(batch):
    ratio = _farm_share_ratio(batch)

    bird_sale_share = _money(sum(
        (_money(sale.total_amount * ratio) for sale in SaleRecord.objects.filter(batch=batch)),
        ZERO,
    ))
    egg_sale_share = _money(sum(
        (_money(sale.total_amount * ratio) for sale in EggSale.objects.filter(batch=batch)),
        ZERO,
    ))
    total_sale_share = _money(bird_sale_share + egg_sale_share)
    total_sale_withdrawn = _money(
        batch.farm_sale_withdrawals.aggregate(total=Sum("amount"))["total"] or ZERO
    )
    sale_balance_due = _money(max(total_sale_share - total_sale_withdrawn, ZERO))
    sale_overdrawn = _money(max(total_sale_withdrawn - total_sale_share, ZERO))

    return {
        "bird_sale_share": bird_sale_share,
        "egg_sale_share": egg_sale_share,
        "total_sale_share": total_sale_share,
        "total_sale_withdrawn": total_sale_withdrawn,
        "sale_balance_due": sale_balance_due,
        "sale_overdrawn": sale_overdrawn,
    }


def _farm_account_snapshot(batch):
    ratio = _farm_share_ratio(batch)
    starting_birds = int(batch.bird_count_initial or 0)
    investor_birds = (
        InvestorAllocation.objects.filter(batch=batch)
        .aggregate(total=Sum("birds_owned"))["total"]
        or 0
    )
    farm_birds = max(starting_birds - int(investor_birds), 0)

    batch_costs = _batch_cost_totals(batch)
    shares = {
        key: _money(value * ratio)
        for key, value in batch_costs.items()
        if key != "total_cost"
    }
    shares["total_cost"] = _money(sum(shares.values(), ZERO))

    total_invested = _money(
        batch.farm_account_payments.aggregate(total=Sum("amount"))["total"] or ZERO
    )
    raw_balance = _money(shares["total_cost"] - total_invested)
    if abs(raw_balance) < ACCOUNT_TOLERANCE:
        raw_balance = ZERO

    outstanding = _money(max(raw_balance, ZERO))
    credit = _money(max(-raw_balance, ZERO))

    if credit > ZERO:
        status = "credit"
        status_label = "Credit"
    elif outstanding <= ZERO and shares["total_cost"] > ZERO:
        status = "paid"
        status_label = "Funded"
    elif total_invested > ZERO:
        status = "partial"
        status_label = "Partial"
    elif shares["total_cost"] > ZERO:
        status = "unpaid"
        status_label = "Unfunded"
    else:
        status = "no_cost"
        status_label = "No Cost"

    sale_proceeds = _farm_sale_proceeds_snapshot(batch)

    return {
        "batch": batch,
        "share_ratio": ratio,
        "share_percentage": (ratio * Decimal("100")).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        ),
        "birds_owned": farm_birds,
        "chick_cost_share": shares["chick_cost"],
        "carriage_cost_share": shares["carriage_cost"],
        "feed_cost_share": shares["feed_cost"],
        "medicine_cost_share": shares["medicine_cost"],
        "expense_cost_share": shares["expense_cost"],
        "total_cost_share": shares["total_cost"],
        "total_invested": total_invested,
        "outstanding": outstanding,
        "credit": credit,
        "status": status,
        "status_label": status_label,
        **sale_proceeds,
    }

def _sale_proceeds_snapshot(allocation):
    """Revenue owed to this investor, kept separate from cost contributions."""
    ratio = _share_ratio(allocation)

    bird_sale_share = _money(sum(
        (_money(sale.total_amount * ratio) for sale in SaleRecord.objects.filter(batch=allocation.batch)),
        ZERO,
    ))
    egg_sale_share = _money(sum(
        (_money(sale.total_amount * ratio) for sale in EggSale.objects.filter(batch=allocation.batch)),
        ZERO,
    ))
    total_sale_share = _money(bird_sale_share + egg_sale_share)
    total_sale_paid = _money(
        allocation.sale_payouts.aggregate(total=Sum("amount"))["total"] or ZERO
    )
    sale_balance_due = _money(max(total_sale_share - total_sale_paid, ZERO))
    sale_overpaid = _money(max(total_sale_paid - total_sale_share, ZERO))

    return {
        "bird_sale_share": bird_sale_share,
        "egg_sale_share": egg_sale_share,
        "total_sale_share": total_sale_share,
        "total_sale_paid": total_sale_paid,
        "sale_balance_due": sale_balance_due,
        "sale_overpaid": sale_overpaid,
    }


def _account_snapshot(allocation):
    ratio = _share_ratio(allocation)
    batch_costs = _batch_cost_totals(allocation.batch)
    sale_proceeds = _sale_proceeds_snapshot(allocation)

    shares = {
        key: _money(value * ratio)
        for key, value in batch_costs.items()
        if key != "total_cost"
    }
    shares["total_cost"] = _money(sum(shares.values(), ZERO))

    total_paid = (
        allocation.account_payments.aggregate(total=Sum("amount"))["total"]
        or ZERO
    )
    total_paid = _money(total_paid)

    raw_balance = _money(shares["total_cost"] - total_paid)

    # The UI displays financial figures rounded to whole rupees.
    # Ignore sub-50-paisa differences for account status so a screen
    # showing Cost Rs 57,934 / Paid Rs 57,934 / Credit Rs 0 does not
    # incorrectly say "Credit" or "Outstanding". Exact payment
    # records and cost calculations are still preserved to the paisa.
    if abs(raw_balance) < ACCOUNT_TOLERANCE:
        raw_balance = ZERO

    outstanding = max(raw_balance, ZERO)
    credit = max(-raw_balance, ZERO)

    if credit > ZERO:
        status = "credit"
        status_label = "Credit"
    elif outstanding <= ZERO and shares["total_cost"] > ZERO:
        status = "paid"
        status_label = "Paid"
    elif total_paid > ZERO:
        status = "partial"
        status_label = "Partial"
    elif shares["total_cost"] > ZERO:
        status = "unpaid"
        status_label = "Unpaid"
    else:
        status = "no_cost"
        status_label = "No Cost"

    return {
        "allocation": allocation,
        "investor_name": _investor_name(allocation),
        "batch": allocation.batch,
        "share_ratio": ratio,
        "share_percentage": (ratio * Decimal("100")).quantize(
            Decimal("0.1"),
            rounding=ROUND_HALF_UP,
        ),
        "birds_owned": allocation.birds_owned,
        "chick_cost_share": shares["chick_cost"],
        "carriage_cost_share": shares["carriage_cost"],
        "feed_cost_share": shares["feed_cost"],
        "medicine_cost_share": shares["medicine_cost"],
        "expense_cost_share": shares["expense_cost"],
        "total_cost_share": shares["total_cost"],
        "total_paid": total_paid,
        "outstanding": _money(outstanding),
        "credit": _money(credit),
        "status": status,
        "status_label": status_label,
        **sale_proceeds,
    }



def _goat_account_snapshot(owner):
    goats = list(
        Goat.objects
        .filter(owner=owner)
        .select_related("owner", "shed")
        .prefetch_related("weight_records")
        .order_by("goat_code", "id")
    )

    purchase_cost = _money(sum((goat.purchase_cost or ZERO for goat in goats), ZERO))

    allocations = GoatCostAllocation.objects.filter(owner_snapshot=owner)
    feed_cost = _money(
        allocations.filter(cost_entry__category="feed")
        .aggregate(total=Sum("amount"))["total"] or ZERO
    )
    medicine_cost = _money(
        allocations.filter(cost_entry__category="medicine")
        .aggregate(total=Sum("amount"))["total"] or ZERO
    )
    expense_cost = _money(
        allocations.filter(cost_entry__category="expense")
        .aggregate(total=Sum("amount"))["total"] or ZERO
    )

    total_cost = _money(purchase_cost + feed_cost + medicine_cost + expense_cost)
    total_paid = _money(
        owner.goat_account_payments.aggregate(total=Sum("amount"))["total"]
        or ZERO
    )

    raw_balance = _money(total_cost - total_paid)
    if abs(raw_balance) < ACCOUNT_TOLERANCE:
        raw_balance = ZERO

    outstanding = _money(max(raw_balance, ZERO))
    credit = _money(max(-raw_balance, ZERO))

    if credit > ZERO:
        status = "credit"
        status_label = "Credit"
    elif outstanding <= ZERO and total_cost > ZERO:
        status = "paid"
        status_label = "Paid"
    elif total_paid > ZERO:
        status = "partial"
        status_label = "Partial"
    elif total_cost > ZERO:
        status = "unpaid"
        status_label = "Unpaid"
    else:
        status = "no_cost"
        status_label = "No Cost"

    owner_name = owner.get_full_name().strip() or owner.username

    return {
        "owner": owner,
        "owner_name": owner_name,
        "goat_count": len(goats),
        "active_goat_count": sum(1 for goat in goats if goat.status == "active"),
        "purchase_cost": purchase_cost,
        "feed_cost": feed_cost,
        "medicine_cost": medicine_cost,
        "expense_cost": expense_cost,
        "total_cost": total_cost,
        "total_paid": total_paid,
        "outstanding": outstanding,
        "credit": credit,
        "status": status,
        "status_label": status_label,
    }

def _authorized_allocation(request, allocation_id):
    allocation = get_object_or_404(
        InvestorAllocation.objects.select_related(
            "batch__shed",
            "investor__user",
        ),
        id=allocation_id,
    )

    if _is_admin(request.user):
        return allocation

    investor_profile = getattr(request.user, "investor_profile", None)
    if investor_profile and allocation.investor_id == investor_profile.id:
        return allocation

    return None


def _build_statement(allocation):
    ratio = _share_ratio(allocation)
    rows = []

    chick_entries = ChickCostEntry.objects.filter(
        batch=allocation.batch
    ).order_by("entry_date", "id")

    for entry in chick_entries:
        if entry.chick_cost and entry.chick_cost > ZERO:
            rows.append({
                "date": entry.entry_date,
                "sort_type": 0,
                "sort_id": entry.id * 10,
                "entry_type": "charge",
                "category": "Chick Cost",
                "description": entry.notes or "Chick purchase",
                "charge": _money(entry.chick_cost * ratio),
                "payment": ZERO,
            })

        if entry.carriage_cost and entry.carriage_cost > ZERO:
            rows.append({
                "date": entry.entry_date,
                "sort_type": 0,
                "sort_id": entry.id * 10 + 1,
                "entry_type": "charge",
                "category": "Carriage",
                "description": entry.notes or "Chick carriage / delivery",
                "charge": _money(entry.carriage_cost * ratio),
                "payment": ZERO,
            })

    for entry in FeedEntry.objects.filter(
        batch=allocation.batch
    ).order_by("entry_date", "id"):
        rows.append({
            "date": entry.entry_date,
            "sort_type": 0,
            "sort_id": 100000 + entry.id,
            "entry_type": "charge",
            "category": "Feed",
            "description": entry.notes or "Feed purchase",
            "charge": _money(entry.amount * ratio),
            "payment": ZERO,
        })

    for entry in MedicineEntry.objects.filter(
        batch=allocation.batch
    ).order_by("entry_date", "id"):
        description = (
            f"{entry.get_medicine_type_display()} · "
            f"{entry.medicine_name}"
        )
        if entry.notes:
            description += f" — {entry.notes}"

        rows.append({
            "date": entry.entry_date,
            "sort_type": 0,
            "sort_id": 200000 + entry.id,
            "entry_type": "charge",
            "category": "Medicine",
            "description": description,
            "charge": _money(entry.amount * ratio),
            "payment": ZERO,
        })

    for entry in Expense.objects.filter(
        batch=allocation.batch
    ).order_by("expense_date", "id"):
        description = entry.get_category_display()
        if entry.description:
            description += f" — {entry.description}"

        rows.append({
            "date": entry.expense_date,
            "sort_type": 0,
            "sort_id": 300000 + entry.id,
            "entry_type": "charge",
            "category": "Expense",
            "description": description,
            "charge": _money(entry.amount * ratio),
            "payment": ZERO,
        })

    for payment in allocation.account_payments.all().order_by(
        "payment_date",
        "id",
    ):
        description = payment.get_payment_method_display()
        if payment.reference:
            description += f" · {payment.reference}"
        if payment.notes:
            description += f" — {payment.notes}"

        rows.append({
            "date": payment.payment_date,
            "sort_type": 1,
            "sort_id": 400000 + payment.id,
            "entry_type": "payment",
            "category": "Payment Received",
            "description": description,
            "charge": ZERO,
            "payment": _money(payment.amount),
            "payment_record": payment,
        })

    rows.sort(
        key=lambda row: (
            row["date"],
            row["sort_type"],
            row["sort_id"],
        )
    )

    running_balance = ZERO
    for row in rows:
        running_balance = _money(
            running_balance
            + row["charge"]
            - row["payment"]
        )
        row["running_balance"] = running_balance

    return rows


def _build_sale_statement(allocation):
    """Sales-only ledger: sale entitlement increases due-to-investor balance; payouts reduce it."""
    ratio = _share_ratio(allocation)
    rows = []

    for sale in SaleRecord.objects.filter(batch=allocation.batch).order_by("sale_date", "id"):
        if getattr(sale, "sale_mode", "counted") == "weight_only":
            details = f"Weight-only bird sale · {sale.total_weight_kg} KG @ Rs {sale.rate_per_kg}/KG"
        else:
            details = f"Bird sale · {int(sale.birds_sold or 0)} birds · {sale.total_weight_kg} KG @ Rs {sale.rate_per_kg}/KG"
        rows.append({
            "date": sale.sale_date,
            "sort_type": 0,
            "sort_id": sale.id,
            "entry_type": "sale",
            "category": "Bird Sale",
            "description": details,
            "sale_share": _money(sale.total_amount * ratio),
            "payout": ZERO,
        })

    for sale in EggSale.objects.filter(batch=allocation.batch).order_by("sale_date", "id"):
        details = f"Egg sale · {sale.eggs_sold} eggs @ Rs {sale.rate_per_egg}/egg"
        if sale.buyer_name:
            details += f" · {sale.buyer_name}"
        rows.append({
            "date": sale.sale_date,
            "sort_type": 0,
            "sort_id": 100000 + sale.id,
            "entry_type": "sale",
            "category": "Egg Sale",
            "description": details,
            "sale_share": _money(sale.total_amount * ratio),
            "payout": ZERO,
        })

    for payout in allocation.sale_payouts.all().order_by("payout_date", "id"):
        description = payout.get_payment_method_display()
        if payout.reference:
            description += f" · {payout.reference}"
        if payout.notes:
            description += f" — {payout.notes}"
        rows.append({
            "date": payout.payout_date,
            "sort_type": 1,
            "sort_id": 200000 + payout.id,
            "entry_type": "payout",
            "category": "Paid to Investor",
            "description": description,
            "sale_share": ZERO,
            "payout": _money(payout.amount),
            "payout_record": payout,
        })

    rows.sort(key=lambda row: (row["date"], row["sort_type"], row["sort_id"]))
    running_balance = ZERO
    for row in rows:
        running_balance = _money(running_balance + row["sale_share"] - row["payout"])
        row["running_balance"] = running_balance

    return rows


def _build_farm_statement(batch):
    ratio = _farm_share_ratio(batch)
    rows = []

    for entry in ChickCostEntry.objects.filter(batch=batch).order_by("entry_date", "id"):
        if entry.chick_cost and entry.chick_cost > ZERO:
            rows.append({
                "date": entry.entry_date,
                "sort_type": 0,
                "sort_id": entry.id * 10,
                "entry_type": "charge",
                "category": "Chick Cost",
                "description": entry.notes or "Chick purchase",
                "charge": _money(entry.chick_cost * ratio),
                "payment": ZERO,
            })
        if entry.carriage_cost and entry.carriage_cost > ZERO:
            rows.append({
                "date": entry.entry_date,
                "sort_type": 0,
                "sort_id": entry.id * 10 + 1,
                "entry_type": "charge",
                "category": "Carriage",
                "description": entry.notes or "Chick carriage / delivery",
                "charge": _money(entry.carriage_cost * ratio),
                "payment": ZERO,
            })

    for entry in FeedEntry.objects.filter(batch=batch).order_by("entry_date", "id"):
        rows.append({
            "date": entry.entry_date,
            "sort_type": 0,
            "sort_id": 100000 + entry.id,
            "entry_type": "charge",
            "category": "Feed",
            "description": entry.notes or "Feed purchase",
            "charge": _money(entry.amount * ratio),
            "payment": ZERO,
        })

    for entry in MedicineEntry.objects.filter(batch=batch).order_by("entry_date", "id"):
        description = f"{entry.get_medicine_type_display()} · {entry.medicine_name}"
        if entry.notes:
            description += f" — {entry.notes}"
        rows.append({
            "date": entry.entry_date,
            "sort_type": 0,
            "sort_id": 200000 + entry.id,
            "entry_type": "charge",
            "category": "Medicine",
            "description": description,
            "charge": _money(entry.amount * ratio),
            "payment": ZERO,
        })

    for entry in Expense.objects.filter(batch=batch).order_by("expense_date", "id"):
        description = entry.get_category_display()
        if entry.description:
            description += f" — {entry.description}"
        rows.append({
            "date": entry.expense_date,
            "sort_type": 0,
            "sort_id": 300000 + entry.id,
            "entry_type": "charge",
            "category": "Expense",
            "description": description,
            "charge": _money(entry.amount * ratio),
            "payment": ZERO,
        })

    for payment in batch.farm_account_payments.all().order_by("payment_date", "id"):
        description = payment.get_payment_method_display()
        if payment.reference:
            description += f" · {payment.reference}"
        if payment.notes:
            description += f" — {payment.notes}"
        rows.append({
            "date": payment.payment_date,
            "sort_type": 1,
            "sort_id": 400000 + payment.id,
            "entry_type": "payment",
            "category": "Farm Contribution",
            "description": description,
            "charge": ZERO,
            "payment": _money(payment.amount),
        })

    rows.sort(key=lambda row: (row["date"], row["sort_type"], row["sort_id"]))
    running_balance = ZERO
    for row in rows:
        running_balance = _money(running_balance + row["charge"] - row["payment"])
        row["running_balance"] = running_balance
    return rows


def _build_farm_sale_statement(batch):
    ratio = _farm_share_ratio(batch)
    rows = []

    for sale in SaleRecord.objects.filter(batch=batch).order_by("sale_date", "id"):
        if getattr(sale, "sale_mode", "counted") == "weight_only":
            details = f"Weight-only bird sale · {sale.total_weight_kg} KG @ Rs {sale.rate_per_kg}/KG"
        else:
            details = f"Bird sale · {int(sale.birds_sold or 0)} birds · {sale.total_weight_kg} KG @ Rs {sale.rate_per_kg}/KG"
        rows.append({
            "date": sale.sale_date,
            "sort_type": 0,
            "sort_id": sale.id,
            "entry_type": "sale",
            "category": "Bird Sale",
            "description": details,
            "sale_share": _money(sale.total_amount * ratio),
            "withdrawal": ZERO,
        })

    for sale in EggSale.objects.filter(batch=batch).order_by("sale_date", "id"):
        details = f"Egg sale · {sale.eggs_sold} eggs @ Rs {sale.rate_per_egg}/egg"
        if sale.buyer_name:
            details += f" · {sale.buyer_name}"
        rows.append({
            "date": sale.sale_date,
            "sort_type": 0,
            "sort_id": 100000 + sale.id,
            "entry_type": "sale",
            "category": "Egg Sale",
            "description": details,
            "sale_share": _money(sale.total_amount * ratio),
            "withdrawal": ZERO,
        })

    for withdrawal in batch.farm_sale_withdrawals.all().order_by("withdrawal_date", "id"):
        description = withdrawal.get_payment_method_display()
        if withdrawal.reference:
            description += f" · {withdrawal.reference}"
        if withdrawal.notes:
            description += f" — {withdrawal.notes}"
        rows.append({
            "date": withdrawal.withdrawal_date,
            "sort_type": 1,
            "sort_id": 200000 + withdrawal.id,
            "entry_type": "withdrawal",
            "category": "Farm Withdrawal",
            "description": description,
            "sale_share": ZERO,
            "withdrawal": _money(withdrawal.amount),
        })

    rows.sort(key=lambda row: (row["date"], row["sort_type"], row["sort_id"]))
    running_balance = ZERO
    for row in rows:
        running_balance = _money(running_balance + row["sale_share"] - row["withdrawal"])
        row["running_balance"] = running_balance
    return rows


@login_required
def investor_accounts(request):
    is_admin = _is_admin(request.user)
    investor_profile = getattr(request.user, "investor_profile", None)

    if not is_admin and investor_profile is None:
        messages.error(
            request,
            "You do not have permission to view investor accounts.",
        )
        return redirect("dashboard")

    # ---------------------------------------------------------
    # POULTRY ACCOUNTS - percentage ownership by batch
    # ---------------------------------------------------------
    allocations = InvestorAllocation.objects.select_related(
        "batch__shed",
        "investor__user",
    )

    if not is_admin:
        allocations = allocations.filter(investor=investor_profile)

    allocations = allocations.order_by(
        "-batch__start_date",
        "batch__batch_number",
        "investor__user__username",
    )

    poultry_accounts = [_account_snapshot(item) for item in allocations]

    poultry_total_cost = _money(sum(
        (item["total_cost_share"] for item in poultry_accounts),
        ZERO,
    ))
    poultry_total_paid = _money(sum(
        (item["total_paid"] for item in poultry_accounts),
        ZERO,
    ))
    poultry_total_outstanding = _money(sum(
        (item["outstanding"] for item in poultry_accounts),
        ZERO,
    ))
    poultry_total_credit = _money(sum(
        (item["credit"] for item in poultry_accounts),
        ZERO,
    ))
    poultry_total_sale_share = _money(sum(
        (item["total_sale_share"] for item in poultry_accounts), ZERO,
    ))
    poultry_total_sale_paid = _money(sum(
        (item["total_sale_paid"] for item in poultry_accounts), ZERO,
    ))
    poultry_total_sale_due = _money(sum(
        (item["sale_balance_due"] for item in poultry_accounts), ZERO,
    ))

    # ---------------------------------------------------------
    # ADMIN / FARM POULTRY ACCOUNTS - residual batch ownership
    # ---------------------------------------------------------
    farm_accounts = []
    farm_total_cost = ZERO
    farm_total_invested = ZERO
    farm_total_outstanding = ZERO
    farm_total_credit = ZERO
    farm_total_sale_share = ZERO
    farm_total_sale_withdrawn = ZERO
    farm_total_sale_due = ZERO

    if is_admin:
        farm_batches = (
            Batch.objects
            .select_related("shed")
            .exclude(shed__shed_type="goat")
            .order_by("-start_date", "batch_number", "id")
        )
        for batch in farm_batches:
            snapshot = _farm_account_snapshot(batch)
            has_history = (
                snapshot["share_ratio"] > ZERO
                or batch.farm_account_payments.exists()
                or batch.farm_sale_withdrawals.exists()
            )
            if has_history:
                farm_accounts.append(snapshot)

        farm_total_cost = _money(sum(
            (item["total_cost_share"] for item in farm_accounts), ZERO,
        ))
        farm_total_invested = _money(sum(
            (item["total_invested"] for item in farm_accounts), ZERO,
        ))
        farm_total_outstanding = _money(sum(
            (item["outstanding"] for item in farm_accounts), ZERO,
        ))
        farm_total_credit = _money(sum(
            (item["credit"] for item in farm_accounts), ZERO,
        ))
        farm_total_sale_share = _money(sum(
            (item["total_sale_share"] for item in farm_accounts), ZERO,
        ))
        farm_total_sale_withdrawn = _money(sum(
            (item["total_sale_withdrawn"] for item in farm_accounts), ZERO,
        ))
        farm_total_sale_due = _money(sum(
            (item["sale_balance_due"] for item in farm_accounts), ZERO,
        ))

    # ---------------------------------------------------------
    # GOAT ACCOUNTS - individual goat ownership
    # ---------------------------------------------------------
    if is_admin:
        goat_owners = (
            User.objects
            .filter(
                investor_profile__isnull=False,
                owned_goats__isnull=False,
            )
            .distinct()
            .order_by("username")
        )
    else:
        goat_owners = User.objects.filter(
            pk=request.user.pk,
            owned_goats__isnull=False,
        )

    goat_accounts = [_goat_account_snapshot(owner) for owner in goat_owners]

    goat_total_cost = _money(sum(
        (item["total_cost"] for item in goat_accounts),
        ZERO,
    ))
    goat_total_paid = _money(sum(
        (item["total_paid"] for item in goat_accounts),
        ZERO,
    ))
    goat_total_outstanding = _money(sum(
        (item["outstanding"] for item in goat_accounts),
        ZERO,
    ))
    goat_total_credit = _money(sum(
        (item["credit"] for item in goat_accounts),
        ZERO,
    ))

    # Combined headline figures. Poultry and Goat balances remain
    # separately auditable in their own account cards/statements.
    total_cost_share = _money(poultry_total_cost + goat_total_cost)
    total_paid = _money(poultry_total_paid + goat_total_paid)
    total_outstanding = _money(
        poultry_total_outstanding + goat_total_outstanding
    )
    total_credit = _money(poultry_total_credit + goat_total_credit)

    outstanding_accounts = (
        sum(1 for item in poultry_accounts if item["outstanding"] > ZERO)
        + sum(1 for item in goat_accounts if item["outstanding"] > ZERO)
    )

    return render(
        request,
        "api/investor_accounts.html",
        {
            # Backward-compatible alias used by older template code.
            "accounts": poultry_accounts,
            "poultry_accounts": poultry_accounts,
            "goat_accounts": goat_accounts,
            "is_admin": is_admin,
            "total_cost_share": total_cost_share,
            "total_paid": total_paid,
            "total_outstanding": total_outstanding,
            "total_credit": total_credit,
            "outstanding_accounts": outstanding_accounts,
            "poultry_total_cost": poultry_total_cost,
            "poultry_total_paid": poultry_total_paid,
            "poultry_total_outstanding": poultry_total_outstanding,
            "poultry_total_credit": poultry_total_credit,
            "poultry_total_sale_share": poultry_total_sale_share,
            "poultry_total_sale_paid": poultry_total_sale_paid,
            "poultry_total_sale_due": poultry_total_sale_due,
            "farm_accounts": farm_accounts,
            "farm_total_cost": farm_total_cost,
            "farm_total_invested": farm_total_invested,
            "farm_total_outstanding": farm_total_outstanding,
            "farm_total_credit": farm_total_credit,
            "farm_total_sale_share": farm_total_sale_share,
            "farm_total_sale_withdrawn": farm_total_sale_withdrawn,
            "farm_total_sale_due": farm_total_sale_due,
            "goat_total_cost": goat_total_cost,
            "goat_total_paid": goat_total_paid,
            "goat_total_outstanding": goat_total_outstanding,
            "goat_total_credit": goat_total_credit,
        },
    )


@login_required
def investor_account_detail(request, allocation_id):
    allocation = _authorized_allocation(request, allocation_id)
    if allocation is None:
        messages.error(
            request,
            "You can only view your own investor account.",
        )
        return redirect("investor_accounts")

    snapshot = _account_snapshot(allocation)
    statement_rows = _build_statement(allocation)
    sale_statement_rows = _build_sale_statement(allocation)

    return render(
        request,
        "api/investor_account_detail.html",
        {
            "account": snapshot,
            "statement_rows": statement_rows,
            "sale_statement_rows": sale_statement_rows,
            "is_admin": _is_admin(request.user),
            "payment_methods": InvestorAccountPayment.PAYMENT_METHOD_CHOICES,
            "sale_payout_methods": InvestorSalePayout.PAYMENT_METHOD_CHOICES,
            "today": timezone.localdate().isoformat(),
        },
    )


@login_required
@require_POST
def record_investor_account_payment(request, allocation_id):
    if not _is_admin(request.user):
        messages.error(
            request,
            "Only Admin can record investor payments.",
        )
        return redirect("investor_accounts")

    allocation = get_object_or_404(
        InvestorAllocation.objects.select_related(
            "batch",
            "investor__user",
        ),
        id=allocation_id,
    )

    try:
        amount = Decimal(
            str(request.POST.get("amount", "0") or "0")
        ).quantize(MONEY)
    except (InvalidOperation, TypeError, ValueError):
        messages.error(request, "Enter a valid payment amount.")
        return redirect(
            "investor_account_detail",
            allocation_id=allocation.id,
        )

    if amount <= ZERO:
        messages.error(
            request,
            "Payment amount must be greater than zero.",
        )
        return redirect(
            "investor_account_detail",
            allocation_id=allocation.id,
        )

    payment_date = request.POST.get("payment_date") or timezone.localdate()
    payment_method = request.POST.get("payment_method") or "bank_transfer"
    valid_methods = {
        key for key, _label in InvestorAccountPayment.PAYMENT_METHOD_CHOICES
    }
    if payment_method not in valid_methods:
        payment_method = "other"

    InvestorAccountPayment.objects.create(
        allocation=allocation,
        payment_date=payment_date,
        amount=amount,
        payment_method=payment_method,
        reference=(request.POST.get("reference") or "").strip(),
        notes=(request.POST.get("notes") or "").strip(),
        recorded_by=request.user,
    )

    investor_name = _investor_name(allocation)
    messages.success(
        request,
        f"Payment of Rs {amount:,.2f} recorded for {investor_name}.",
    )

    return redirect(
        "investor_account_detail",
        allocation_id=allocation.id,
    )

@login_required
@require_POST
def record_investor_sale_payout(request, allocation_id):
    if not _is_admin(request.user):
        messages.error(request, "Only Admin can record sale payouts to investors.")
        return redirect("investor_accounts")

    allocation = get_object_or_404(
        InvestorAllocation.objects.select_related("batch", "investor__user"),
        id=allocation_id,
    )
    sale_position = _sale_proceeds_snapshot(allocation)

    try:
        amount = Decimal(str(request.POST.get("amount", "0") or "0")).quantize(MONEY)
    except (InvalidOperation, TypeError, ValueError):
        messages.error(request, "Enter a valid payout amount.")
        return redirect("investor_account_detail", allocation_id=allocation.id)

    if amount <= ZERO:
        messages.error(request, "Payout amount must be greater than zero.")
        return redirect("investor_account_detail", allocation_id=allocation.id)

    if sale_position["sale_balance_due"] <= ZERO:
        messages.error(request, "There is currently no unpaid sale amount due to this investor.")
        return redirect("investor_account_detail", allocation_id=allocation.id)

    if amount > sale_position["sale_balance_due"] + MONEY:
        messages.error(
            request,
            f"Payout cannot exceed the current sale balance of Rs {sale_position['sale_balance_due']:,.2f}.",
        )
        return redirect("investor_account_detail", allocation_id=allocation.id)

    payout_date = request.POST.get("payout_date") or timezone.localdate()
    payment_method = request.POST.get("payment_method") or "bank_transfer"
    valid_methods = {key for key, _ in InvestorSalePayout.PAYMENT_METHOD_CHOICES}
    if payment_method not in valid_methods:
        payment_method = "other"

    InvestorSalePayout.objects.create(
        allocation=allocation,
        payout_date=payout_date,
        amount=amount,
        payment_method=payment_method,
        reference=(request.POST.get("reference") or "").strip(),
        notes=(request.POST.get("notes") or "").strip(),
        recorded_by=request.user,
    )

    investor_name = _investor_name(allocation)
    messages.success(
        request,
        f"Sale payout of Rs {amount:,.2f} recorded as PAID TO {investor_name}.",
    )
    return redirect("investor_account_detail", allocation_id=allocation.id)



@login_required
def farm_account_detail(request, batch_id):
    if not _is_admin(request.user):
        messages.error(request, "Only Admin can view the Farm account.")
        return redirect("investor_accounts")

    batch = get_object_or_404(Batch.objects.select_related("shed"), id=batch_id)
    account = _farm_account_snapshot(batch)
    statement_rows = _build_farm_statement(batch)
    sale_statement_rows = _build_farm_sale_statement(batch)

    return render(
        request,
        "api/farm_account_detail.html",
        {
            "account": account,
            "statement_rows": statement_rows,
            "sale_statement_rows": sale_statement_rows,
            "payment_methods": FarmAccountPayment.PAYMENT_METHOD_CHOICES,
            "withdrawal_methods": FarmSaleWithdrawal.PAYMENT_METHOD_CHOICES,
            "today": timezone.localdate().isoformat(),
            "is_admin": True,
        },
    )


@login_required
@require_POST
def record_farm_account_payment(request, batch_id):
    if not _is_admin(request.user):
        messages.error(request, "Only Admin can record Farm contributions.")
        return redirect("investor_accounts")

    batch = get_object_or_404(Batch, id=batch_id)

    try:
        amount = Decimal(str(request.POST.get("amount", "0") or "0")).quantize(MONEY)
    except (InvalidOperation, TypeError, ValueError):
        messages.error(request, "Enter a valid contribution amount.")
        return redirect("farm_account_detail", batch_id=batch.id)

    if amount <= ZERO:
        messages.error(request, "Contribution amount must be greater than zero.")
        return redirect("farm_account_detail", batch_id=batch.id)

    payment_date = request.POST.get("payment_date") or timezone.localdate()
    payment_method = request.POST.get("payment_method") or "bank_transfer"
    valid_methods = {key for key, _ in FarmAccountPayment.PAYMENT_METHOD_CHOICES}
    if payment_method not in valid_methods:
        payment_method = "other"

    FarmAccountPayment.objects.create(
        batch=batch,
        payment_date=payment_date,
        amount=amount,
        payment_method=payment_method,
        reference=(request.POST.get("reference") or "").strip(),
        notes=(request.POST.get("notes") or "").strip(),
        recorded_by=request.user,
    )

    messages.success(
        request,
        f"Farm contribution of Rs {amount:,.2f} recorded for Batch #{batch.batch_number}.",
    )
    return redirect("farm_account_detail", batch_id=batch.id)


@login_required
@require_POST
def record_farm_sale_withdrawal(request, batch_id):
    if not _is_admin(request.user):
        messages.error(request, "Only Admin can record Farm sale withdrawals.")
        return redirect("investor_accounts")

    batch = get_object_or_404(Batch, id=batch_id)
    sale_position = _farm_sale_proceeds_snapshot(batch)

    try:
        amount = Decimal(str(request.POST.get("amount", "0") or "0")).quantize(MONEY)
    except (InvalidOperation, TypeError, ValueError):
        messages.error(request, "Enter a valid withdrawal amount.")
        return redirect("farm_account_detail", batch_id=batch.id)

    if amount <= ZERO:
        messages.error(request, "Withdrawal amount must be greater than zero.")
        return redirect("farm_account_detail", batch_id=batch.id)

    if sale_position["sale_balance_due"] <= ZERO:
        messages.error(request, "There is currently no Farm sale balance available to withdraw.")
        return redirect("farm_account_detail", batch_id=batch.id)

    if amount > sale_position["sale_balance_due"] + MONEY:
        messages.error(
            request,
            f"Withdrawal cannot exceed the current Farm sale balance of Rs {sale_position['sale_balance_due']:,.2f}.",
        )
        return redirect("farm_account_detail", batch_id=batch.id)

    withdrawal_date = request.POST.get("withdrawal_date") or timezone.localdate()
    payment_method = request.POST.get("payment_method") or "bank_transfer"
    valid_methods = {key for key, _ in FarmSaleWithdrawal.PAYMENT_METHOD_CHOICES}
    if payment_method not in valid_methods:
        payment_method = "other"

    FarmSaleWithdrawal.objects.create(
        batch=batch,
        withdrawal_date=withdrawal_date,
        amount=amount,
        payment_method=payment_method,
        reference=(request.POST.get("reference") or "").strip(),
        notes=(request.POST.get("notes") or "").strip(),
        recorded_by=request.user,
    )

    messages.success(
        request,
        f"Farm sale withdrawal of Rs {amount:,.2f} recorded for Batch #{batch.batch_number}.",
    )
    return redirect("farm_account_detail", batch_id=batch.id)
