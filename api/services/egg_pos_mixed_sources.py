"""Sell several source SKUs as one customer-facing product without losing source-level COGS.

The SKU on each POS/online sale line is the primary selling product. Physical
stock and actual cost are traced through EggPOSSaleAllocation -> InventoryLot.
"""
from decimal import Decimal
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from api.models.egg_pos import EggPOSProduct, EggPOSInventoryLot, EggPOSSaleAllocation
from api.models.online_shop import OnlineOrderItemSource, OnlineOrder


def primary_id(product_id):
    product = EggPOSProduct.objects.only('id', 'selling_product_id').get(pk=product_id)
    return product.selling_product_id or product.pk


def source_products(product_id):
    root = primary_id(product_id)
    return EggPOSProduct.objects.filter(Q(pk=root) | Q(selling_product_id=root)).order_by('id')


def source_ids(product_id):
    return list(source_products(product_id).values_list('id', flat=True))


def source_available(source_id, as_of=None, *, lock=False):
    as_of = as_of or timezone.localdate()
    qs = EggPOSInventoryLot.objects.filter(
        product_id=source_id, quantity_remaining__gt=0, received_date__lte=as_of,
    ).filter(Q(expiry_date__isnull=True) | Q(expiry_date__gte=as_of)).order_by('received_date', 'id')
    if lock:
        qs = qs.select_for_update()
    return sum(lot.quantity_remaining for lot in qs)


def packed_source_reserved(source_id, excluding_order=None):
    qs = OnlineOrderItemSource.objects.filter(
        source_product_id=source_id,
        item__order__status__in=(OnlineOrder.PACKED, OnlineOrder.DISPATCHED),
    )
    if excluding_order is not None:
        qs = qs.exclude(item__order_id=excluding_order)
    return int(qs.aggregate(q=Sum('quantity'))['q'] or 0)


def group_available(product_id, as_of=None, *, lock=False):
    ids = source_ids(product_id)
    as_of = as_of or timezone.localdate()
    qs = EggPOSInventoryLot.objects.filter(
        product_id__in=ids, quantity_remaining__gt=0, received_date__lte=as_of,
    ).filter(Q(expiry_date__isnull=True) | Q(expiry_date__gte=as_of)).order_by('received_date','id')
    if lock:
        qs = qs.select_for_update()
    return sum(lot.quantity_remaining for lot in qs)


@transaction.atomic
def allocate_selected_sources(sale_item, sale_date, quantities, *, excluding_online_order=None):
    """Deduct exact source SKU quantities; FIFO is applied *within each SKU*.

    The caller must first enforce total sale vs all active online reservations.
    This allocator additionally respects source-specific packed holds.
    """
    from api.services.egg_pos import money

    try:
        selected = {int(key): int(qty) for key, qty in quantities.items() if int(qty) != 0}
    except (TypeError, ValueError, AttributeError):
        raise ValueError('Enter whole-number egg quantities for each source SKU.')
    if not selected or any(qty < 0 for qty in selected.values()):
        raise ValueError('Select a positive quantity of eggs from the source SKUs.')
    if sum(selected.values()) != sale_item.quantity:
        raise ValueError('Source allocations must add up to the exact eggs on the sale line.')
    allowed = set(source_ids(sale_item.product_id))
    if set(selected) - allowed:
        raise ValueError('A selected source SKU does not belong to this selling product.')

    # Lock all candidate lots in a stable order; do not create partial allocations.
    candidates = list(EggPOSInventoryLot.objects.select_for_update().filter(
        product_id__in=selected, quantity_remaining__gt=0,
        received_date__lte=sale_date,
    ).filter(Q(expiry_date__isnull=True) | Q(expiry_date__gte=sale_date)).order_by('product_id','received_date','id'))
    by_source = {source_id: [] for source_id in selected}
    for lot in candidates:
        by_source[lot.product_id].append(lot)

    cogs = Decimal('0.00')
    for source_id, needed in sorted(selected.items()):
        lots = by_source[source_id]
        physical = sum(lot.quantity_remaining for lot in lots)
        held = packed_source_reserved(source_id, excluding_order=excluding_online_order)
        if needed > physical - held:
            source = EggPOSProduct.objects.get(pk=source_id)
            raise ValueError(f'{source.sku}: only {max(0,physical-held)} unreserved eggs available; {needed} requested.')
        remaining = needed
        for lot in lots:
            if not remaining:
                break
            take = min(remaining, lot.quantity_remaining)
            if not take:
                continue
            lot.quantity_remaining -= take
            lot.save(update_fields=['quantity_remaining'])
            amount = money(Decimal(take) * lot.unit_cost)
            EggPOSSaleAllocation.objects.create(
                sale_item=sale_item, lot=lot, quantity=take,
                unit_cost=lot.unit_cost, cogs_amount=amount,
            )
            cogs += amount
            remaining -= take
    sale_item.cogs_amount = money(cogs)
    sale_item.save(update_fields=['cogs_amount'])
    return sale_item.cogs_amount
