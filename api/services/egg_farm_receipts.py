"""Farm Egg Management receipts derived from recorded source transactions.

No duplicate farm-side payment is created: one transfer payment is represented
exactly once in the source EggPOSFarmTransferPayment row and is mirrored here
for farm cash/bank reporting. This is a RECEIPTS report, not an available-cash
balance (expenses/withdrawals have their own accounting records).
"""
from decimal import Decimal
from django.db.models import Q

from api.models.eggs import EggSale
from api.models.egg_pos import EggPOSFarmTransferPayment

MONEY = Decimal("0.01")
ZERO = Decimal("0.00")


def receipt_channel(method):
    if method == "cash":
        return "cash"
    if method in {"bank_transfer", "easypaisa", "jazzcash"}:
        return "bank_wallet"
    return "unclassified"


def get_farm_egg_receipts(batches, limit=30):
    """Return all recorded egg receipts for allowed batches, without DB writes.

    Cash/Bank receipts from direct farm sales and actual payments from Egg POS;
    excludes credit sales (unpaid) and voided records. A single POS payment
    appears only as a collection, not a second sale.
    """
    batch_ids = [b.pk for b in batches]
    totals = {
        "direct_receipts": ZERO,
        "pos_receipts": ZERO,
        "gross_receipts": ZERO,
        "cash_receipts": ZERO,
        "bank_wallet_receipts": ZERO,
        "unclassified_receipts": ZERO,
    }
    rows = []
    if not batch_ids:
        return {**totals, "rows": rows}

    direct_sales = (
        EggSale.objects.filter(batch_id__in=batch_ids, is_voided=False)
        .exclude(payment_method="credit")
        .select_related("batch").order_by("-sale_date", "-pk")
    )
    for sale in direct_sales:
        amount = Decimal(sale.total_amount or ZERO).quantize(MONEY)
        if amount <= ZERO:
            continue
        channel = receipt_channel(sale.payment_method)
        totals["direct_receipts"] += amount
        totals[{"cash": "cash_receipts", "bank_wallet": "bank_wallet_receipts", "unclassified": "unclassified_receipts"}[channel]] += amount
        rows.append({
            "date": sale.sale_date,
            "source": "Direct Egg Sale",
            "reference": f"EGG-SALE-{sale.pk}",
            "batch": sale.batch.batch_number,
            "channel": channel,
            "method": sale.get_payment_method_display(),
            "amount": amount,
            "detail": sale.buyer_name or "Direct egg customer",
            "pos_transfer_id": None,
            "source_key": f"egg_sale:{sale.pk}",
        })

    transfer_payments = (
        EggPOSFarmTransferPayment.objects.filter(
            transfer__batch_id__in=batch_ids,
            transfer__is_voided=False,
        )
        .select_related("transfer__batch").order_by("-payment_date", "-pk")
    )
    for payment in transfer_payments:
        amount = Decimal(payment.amount or ZERO).quantize(MONEY)
        if amount <= ZERO:
            continue
        channel = receipt_channel(payment.payment_method)
        totals["pos_receipts"] += amount
        totals[{"cash": "cash_receipts", "bank_wallet": "bank_wallet_receipts", "unclassified": "unclassified_receipts"}[channel]] += amount
        rows.append({
            "date": payment.payment_date,
            "source": "Egg POS Transfer Payment",
            "reference": payment.reference or f"PAY-{payment.pk}",
            "batch": payment.transfer.batch.batch_number,
            "channel": channel,
            "method": payment.get_payment_method_display(),
            "amount": amount,
            "detail": payment.transfer.transfer_number or f"RNET-{payment.transfer_id:05d}",
            "pos_transfer_id": payment.transfer_id,
            "source_key": f"egg_pos_transfer_payment:{payment.pk}",
        })

    totals["gross_receipts"] = totals["direct_receipts"] + totals["pos_receipts"]
    rows.sort(key=lambda r: (r["date"], r["source_key"]), reverse=True)
    return {**totals, "rows": rows[:limit] if limit is not None else rows, "receipt_count": len(rows)}
