"""Read-only per-SKU sales and booking report for RayNoor Retail POS.

Important: pre-packing web reservations are for a *selling product*, not a source
SKU. Never assign those quantities to a farm/purchased source until packed.
Physical quantities are eggs for egg SKUs and sealed jars for retail SKUs.
"""
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Q, Sum
from django.utils import timezone

from api.models.egg_pos import EggPOSProduct, EggPOSInventoryLot, EggPOSSaleItem
from api.models.egg_pos_prebooking import EggPOSManualPrebooking, EggPOSManualPrebookingLine, UNIT_MULTIPLIER, EGG_UNITS
from api.models.online_shop import OnlineOrder, OnlineOrderItem, OnlineOrderItemSource


ZERO = Decimal('0')
CENTS = Decimal('0.01')


def money(value):
    return Decimal(value or 0).quantize(CENTS, rounding=ROUND_HALF_UP)


def make_report(start_date, end_date, category='all'):
    """Report: dated finalized sales; live open reservations and stock.

    This function is intentionally read-only: it never allocates or releases
    stock, updates an order, or posts to the general ledger.
    """
    products = list(EggPOSProduct.objects.all().order_by('category', 'name', 'pk'))
    by_id = {p.pk: p for p in products}
    root_id = {p.pk: p.selling_product_id or p.pk for p in products}
    group_sizes = defaultdict(int)
    for p in products:
        group_sizes[root_id[p.pk]] += 1

    def blank_row(product):
        return {
            'product': product, 'root_id': root_id[product.pk],
            'on_hand': 0, 'fixed_booked': 0, 'online_booked': 0,
            'available': 0, 'sold': 0, 'revenue_raw': ZERO,
            'cogs_raw': ZERO, 'revenue': ZERO, 'cogs': ZERO,
            'profit': ZERO, 'grouped': group_sizes[root_id[product.pk]] > 1,
        }

    rows_by_id = {p.pk: blank_row(p) for p in products}
    today = timezone.localdate()
    # Exclude future and expired lots, just like the POS stock availability.
    for record in EggPOSInventoryLot.objects.filter(
        quantity_remaining__gt=0, received_date__lte=today,
    ).filter(Q(expiry_date__isnull=True) | Q(expiry_date__gte=today)).values(
        'product_id',
    ).annotate(total=Sum('quantity_remaining')):
        if record['product_id'] in rows_by_id:
            rows_by_id[record['product_id']]['on_hand'] = int(record['total'] or 0)

    # A reserved but unconfirmed web checkout has a short expiry. Confirmed,
    # packed and dispatched orders reserve until fulfilled or cancelled.
    now = timezone.now()
    live = (Q(order__status__in=[OnlineOrder.CONFIRMED, OnlineOrder.PACKED, OnlineOrder.DISPATCHED]) |
            Q(order__status=OnlineOrder.RESERVED, order__reserved_until__gt=now))
    online_by_group = defaultdict(int)
    for record in OnlineOrderItem.objects.filter(live, order__pos_sale__isnull=True).values(
        'product_id',
    ).annotate(total=Sum('egg_quantity')):
        if record['product_id'] in root_id:
            online_by_group[root_id[record['product_id']]] += int(record['total'] or 0)

    # Physical SKU holds are known *only after* packing. Their booked counts
    # must not be included again on top of the group-level reservation.
    fixed_by_source = defaultdict(int)
    for record in OnlineOrderItemSource.objects.filter(
        item__order__status__in=(OnlineOrder.PACKED, OnlineOrder.DISPATCHED),
        item__order__pos_sale__isnull=True,
    ).values('source_product_id').annotate(total=Sum('quantity')):
        fixed_by_source[record['source_product_id']] += int(record['total'] or 0)

    for product_id, row in rows_by_id.items():
        row['fixed_booked'] = fixed_by_source[product_id]
        # For standalone SKUs, an unallocated online order still has just one
        # possible source. For grouped eggs, only packed holds are SKU-specific.
        row['online_booked'] = (row['fixed_booked'] if row['grouped']
                                else online_by_group[root_id[product_id]])
        row['available'] = max(0, row['on_hand'] - row['online_booked'])

    # Actual source SKU from FIFO lot allocation, not the product name on the
    # customer-facing invoice. This correctly splits 6+6 and 10+2 cartons.
    # Include older unallocated invoice lines using their original product SKU.
    items = EggPOSSaleItem.objects.filter(
        sale__sale_date__gte=start_date, sale__sale_date__lte=end_date,
        sale__is_reversed=False,
    ).select_related('sale').prefetch_related('lot_allocations__lot')
    for item in items:
        quantity = int(item.quantity or 0)
        if quantity <= 0:
            continue
        subtotal = Decimal(item.sale.subtotal or 0)
        discount = Decimal(item.sale.discount_amount or 0)
        gross = Decimal(item.line_total or 0)
        # Distribute invoice-level discounts among merchandise lines. Delivery
        # revenue is deliberately excluded from SKU revenue/profit.
        net_line = gross * (subtotal - discount) / subtotal if subtotal > 0 else gross
        allocations = list(item.lot_allocations.all())
        assigned = 0
        allocated_cost = ZERO
        for alloc in allocations:
            source = alloc.lot.product_id
            take = int(alloc.quantity or 0)
            if take <= 0 or source not in rows_by_id:
                continue
            cost = Decimal(alloc.cogs_amount or 0)
            row = rows_by_id[source]
            row['sold'] += take
            row['revenue_raw'] += net_line * take / quantity
            row['cogs_raw'] += cost
            assigned += take
            allocated_cost += cost
        remaining = quantity - assigned
        if remaining > 0 and item.product_id in rows_by_id:
            row = rows_by_id[item.product_id]
            row['sold'] += remaining
            row['revenue_raw'] += net_line * remaining / quantity
            row['cogs_raw'] += Decimal(item.cogs_amount or 0) - allocated_cost

    # Manual/social prebookings are enquiries, NOT physical stock holds. Show
    # them separately and never subtract them from available-to-sell stock.
    manual_by_group = defaultdict(int)
    for record in EggPOSManualPrebookingLine.objects.filter(
        booking__status__in=('awaiting_stock', 'contact_customer', 'confirmed'),
    ).values('product_id', 'unit').annotate(qty=Sum('quantity')):
        product_id = record['product_id']
        if product_id not in root_id:
            continue
        product = by_id[product_id]
        unit = record['unit']
        if product.is_egg:
            # The egg units are sold in physical eggs; reject mismatched jar
            # entries rather than counting one jar as one egg.
            if unit not in EGG_UNITS:
                continue
            physical_qty = int(record['qty'] or 0) * UNIT_MULTIPLIER[unit]
        else:
            # Ghee/honey are finished goods: a prebooking for two jars is
            # two manual requests, never 2 trays (60 imaginary eggs).
            # Ignore legacy records accidentally saved with an egg unit.
            if unit != 'jar':
                continue
            physical_qty = int(record['qty'] or 0)
        manual_by_group[root_id[product_id]] += physical_qty

    groups_by_id = {}
    for p in products:
        root = root_id[p.pk]
        group = groups_by_id.setdefault(root, {
            'primary': by_id.get(root, p), 'category': p.category,
            'sources': [], 'on_hand': 0, 'online_booked': online_by_group[root],
            'manual_requests': manual_by_group[root], 'fixed_booked': 0,
            'unallocated_booked': 0, 'available': 0,
            'sold': 0, 'revenue_raw': ZERO, 'cogs_raw': ZERO,
            'revenue': ZERO, 'cogs': ZERO, 'profit': ZERO,
        })
        row = rows_by_id[p.pk]
        row['revenue'] = money(row['revenue_raw'])
        row['cogs'] = money(row['cogs_raw'])
        row['profit'] = money(row['revenue_raw'] - row['cogs_raw'])
        group['sources'].append(row)
        group['on_hand'] += row['on_hand']
        group['fixed_booked'] += row['fixed_booked']
        group['sold'] += row['sold']
        group['revenue_raw'] += row['revenue_raw']
        group['cogs_raw'] += row['cogs_raw']

    for group in groups_by_id.values():
        group['unallocated_booked'] = max(0, group['online_booked'] - group['fixed_booked'])
        group['available'] = max(0, group['on_hand'] - group['online_booked'])
        group['revenue'] = money(group['revenue_raw'])
        group['cogs'] = money(group['cogs_raw'])
        group['profit'] = money(group['revenue_raw'] - group['cogs_raw'])

    groups = [g for g in groups_by_id.values()
              if category == 'all' or g['category'] == category]
    rows = [r for g in groups for r in g['sources']]
    return {
        'rows': rows, 'groups': groups,
        'group_count': len(groups),
        'revenue': money(sum((g['revenue_raw'] for g in groups), ZERO)),
        'cogs': money(sum((g['cogs_raw'] for g in groups), ZERO)),
        'profit': money(sum((g['revenue_raw'] - g['cogs_raw'] for g in groups), ZERO)),
        'eggs_sold': sum(g['sold'] for g in groups if g['category'] == 'egg'),
        'jars_sold': sum(g['sold'] for g in groups if g['category'] != 'egg'),
        'eggs_booked': sum(g['online_booked'] for g in groups if g['category'] == 'egg'),
        'jars_booked': sum(g['online_booked'] for g in groups if g['category'] != 'egg'),
    }
