from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from api.models.egg_pos import (
    EggPOSFarmTransfer,
    EggPOSSale,
    EggPOSInventoryLot,
    EggPOSSaleAllocation,
)
from api.models.eggs import EggProductionEntry, EggSale, EggStockWastage


ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def money(value):
    return Decimal(value or 0).quantize(CENT)


def is_admin(user):
    return bool(user.is_superuser or user.is_staff)


def can_sell(user):
    if not user.is_authenticated:
        return False
    if is_admin(user):
        return True
    access = getattr(user, "egg_pos_access", None)
    return bool(access and access.is_active and access.can_sell)


def can_manage(user):
    if not user.is_authenticated:
        return False
    if is_admin(user):
        return True
    access = getattr(user, "egg_pos_access", None)
    return bool(access and access.is_active and access.can_manage)


def available_product_stock(product, as_of_date=None):
    as_of_date = as_of_date or timezone.localdate()
    qs = EggPOSInventoryLot.objects.filter(
        product=product,
        quantity_remaining__gt=0,
        received_date__lte=as_of_date,
    ).filter(Q(expiry_date__isnull=True) | Q(expiry_date__gte=as_of_date))
    return int(qs.aggregate(total=Sum("quantity_remaining"))["total"] or 0)


def total_product_stock(product):
    return int(
        EggPOSInventoryLot.objects.filter(
            product=product,
            quantity_remaining__gt=0,
        ).aggregate(total=Sum("quantity_remaining"))["total"]
        or 0
    )


def farm_egg_stock(batch):
    production = EggProductionEntry.objects.filter(batch=batch).aggregate(
        collected=Sum("eggs_collected"),
        damaged=Sum("damaged_eggs"),
    )
    collected = int(production["collected"] or 0)
    damaged = int(production["damaged"] or 0)
    usable = max(collected - damaged, 0)
    old_direct_sales = int(
        EggSale.objects.filter(batch=batch, is_voided=False).aggregate(total=Sum("eggs_sold"))["total"]
        or 0
    )
    wasted = int(EggStockWastage.objects.filter(batch=batch).aggregate(total=Sum("quantity"))["total"] or 0)
    transferred = 0
    for transfer in EggPOSFarmTransfer.objects.filter(batch=batch, is_voided=False).prefetch_related("items"):
        transferred += sum(int(item.quantity or 0) for item in transfer.items.all())
    return {
        "usable": usable,
        "direct_sales": old_direct_sales,
        "transferred": transferred,
        "wasted": wasted,
        "available": max(usable - old_direct_sales - transferred - wasted, 0),
    }


def make_lot_code(prefix, source_id, item_number):
    return f"{prefix}-{timezone.localdate():%y%m%d}-{int(source_id):05d}-{int(item_number):02d}"


@transaction.atomic
def allocate_fifo_to_sale_item(sale_item, sale_date):
    quantity_needed = int(sale_item.quantity or 0)
    if quantity_needed <= 0:
        raise ValueError("Sale quantity must be greater than zero.")

    lots = list(
        EggPOSInventoryLot.objects.select_for_update()
        .filter(
            product=sale_item.product,
            quantity_remaining__gt=0,
            received_date__lte=sale_date,
        )
        .filter(Q(expiry_date__isnull=True) | Q(expiry_date__gte=sale_date))
        .order_by("received_date", "id")
    )

    available = sum(int(lot.quantity_remaining or 0) for lot in lots)
    if available < quantity_needed:
        raise ValueError(
            f"Only {available} {sale_item.product.name} eggs are available; "
            f"{quantity_needed} requested."
        )

    cogs = ZERO
    remaining = quantity_needed
    for lot in lots:
        if remaining <= 0:
            break
        take = min(remaining, int(lot.quantity_remaining or 0))
        if take <= 0:
            continue

        lot.quantity_remaining -= take
        lot.save(update_fields=["quantity_remaining"])

        allocation_cogs = money(Decimal(take) * Decimal(lot.unit_cost or 0))
        EggPOSSaleAllocation.objects.create(
            sale_item=sale_item,
            lot=lot,
            quantity=take,
            unit_cost=lot.unit_cost,
            cogs_amount=allocation_cogs,
        )
        cogs += allocation_cogs
        remaining -= take

    sale_item.cogs_amount = money(cogs)
    sale_item.save(update_fields=["cogs_amount"])
    return sale_item.cogs_amount


@transaction.atomic
def reverse_sale(sale, reversed_by, reason):
    """Reverse a POS sale without deleting its audit trail."""
    sale = (EggPOSSale.objects.select_for_update()
            .prefetch_related("items__lot_allocations__lot", "payments")
            .get(pk=sale.pk))
    if sale.is_reversed:
        raise ValueError(f"{sale.sale_number} is already reversed.")
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("Please enter a reversal reason.")

    # Put exactly the FIFO quantities consumed by this invoice back into their lots.
    for item in sale.items.all():
        for allocation in item.lot_allocations.all():
            lot = EggPOSInventoryLot.objects.select_for_update().get(pk=allocation.lot_id)
            lot.quantity_remaining += int(allocation.quantity or 0)
            lot.save(update_fields=["quantity_remaining"])

    sale.is_reversed = True
    sale.reversed_at = timezone.now()
    sale.reversed_by = reversed_by
    sale.reversal_reason = reason[:255]
    sale.save(update_fields=["is_reversed", "reversed_at", "reversed_by", "reversal_reason"])

    # Remove the financial effect while retaining the source sale/payment rows for audit.
    from api.models.accounting import JournalEntry
    JournalEntry.objects.filter(source_key=f"egg_pos:sale:{sale.id}").delete()
    for payment in sale.payments.all():
        JournalEntry.objects.filter(source_key=f"egg_pos:sale_payment:{payment.id}").delete()
    return sale
