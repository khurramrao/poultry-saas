"""Online egg reservations and fulfillment against the existing POS FIFO stock.

Public cart does not modify stock. A short-lived order reserves product quantities;
POS sales and other online orders must subtract active reservations.
"""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from api.models.egg_pos import (
    EggPOSInventoryLot, EggPOSProduct, EggPOSSale, EggPOSSaleItem,
    EggPOSSalePayment,
)
from api.models.online_shop import OnlineOffer, OnlineOrder, OnlineOrderItem, OnlineOrderItemSource, OnlineOrderEvent
from api.services.egg_pos_mixed_sources import primary_id, source_ids, source_available, packed_source_reserved, allocate_selected_sources
from api.services.egg_pos import allocate_fifo_to_sale_item
from api.services.egg_pos_accounting import sync_sale_invoice, sync_sale_payment

CENT = Decimal('0.01')
# Egg merchandise subtotal, before customer delivery fees or courier costs.
MIN_EGGS_SUBTOTAL = Decimal('1000.00')


def cash(value):
    return Decimal(value).quantize(CENT)


def reserved_quantity(product_id, excluding_order=None):
    now = timezone.now()
    group_ids = source_ids(product_id)
    qs = OnlineOrderItem.objects.filter(product_id__in=group_ids).filter(
        Q(order__status__in=[OnlineOrder.CONFIRMED, OnlineOrder.PACKED, OnlineOrder.DISPATCHED])
        | Q(order__status=OnlineOrder.RESERVED, order__reserved_until__gt=now)
    )
    if excluding_order:
        qs = qs.exclude(order_id=excluding_order)
    return int(qs.aggregate(s=Sum('egg_quantity'))['s'] or 0)


def gross_available(product_id, as_of=None, lock=False):
    as_of = as_of or timezone.localdate()
    lots = EggPOSInventoryLot.objects.filter(
        product_id__in=source_ids(product_id), quantity_remaining__gt=0, received_date__lte=as_of,
    ).filter(Q(expiry_date__isnull=True) | Q(expiry_date__gte=as_of)).order_by('id')
    if lock:
        lots = lots.select_for_update()
    # Force evaluation of locked rows, rather than doing an aggregate without a lock.
    return sum(int(lot.quantity_remaining) for lot in lots)


def free_available(product_id, lock=False, excluding_order=None):
    total = gross_available(product_id, lock=lock)
    return max(0, total - reserved_quantity(product_id, excluding_order=excluding_order))


@transaction.atomic
def checkout_order(cart, *, account, name, phone, address, zone, note):
    if not zone.is_active or not (zone.own_rider_allowed or zone.third_party_allowed):
        raise ValueError('Delivery is not available in this area yet.')
    if not cart or len(cart) > 20:
        raise ValueError('Add at least one RayNoor product to your cart.')
    requests = []
    by_product = defaultdict(int)
    subtotal = Decimal('0.00')
    offer_ids = []
    for raw_id, raw_count in cart.items():
        try:
            offer_id, count = int(raw_id), int(raw_count)
        except (ValueError, TypeError):
            raise ValueError('Your cart contains an invalid item.')
        if count < 1 or count > 24:
            raise ValueError('Choose between 1 and 24 packs per product.')
        offer_ids.append(offer_id)
        requests.append((offer_id, count))
    if len(offer_ids) != len(set(offer_ids)):
        raise ValueError('Your cart contains duplicate items.')
    offers = {offer.pk: offer for offer in OnlineOffer.objects.select_related('product').filter(
        pk__in=offer_ids, is_active=True, product__is_active=True)}
    if len(offers) != len(offer_ids):
        raise ValueError('A product is no longer offered. Please review your cart.')
    if sum(count for _, count in requests) > 60:
        raise ValueError('Please contact RayNoor for large orders.')
    clean = []
    for offer_id, count in requests:
        offer = offers[offer_id]
        if offer.price_per_pack <= 0:
            raise ValueError(f'{offer} is not yet available for purchase.')
        if (offer.product.is_egg and offer.pack_size not in (6, 12, 30)) or (
                not offer.product.is_egg and offer.pack_size != 1):
            raise ValueError(f'{offer.product.name} has an invalid online pack configuration.')
        eggs = int(offer.pack_size) * count
        line_total = cash(offer.price_per_pack * count)
        by_product[primary_id(offer.product_id)] += eggs
        subtotal += line_total
        clean.append((offer, count, eggs, line_total))
    subtotal = cash(subtotal)
    # Apply both the RayNoor-wide floor and any higher area-specific minimum.
    # Do not count delivery toward the merchandise minimum.
    minimum = max(MIN_EGGS_SUBTOTAL, cash(zone.minimum_order))
    if subtotal < minimum:
        missing = cash(minimum - subtotal)
        raise ValueError(
            f'Minimum retail order for {zone.name} is Rs {minimum:,.0f} before delivery. '
            f'Add Rs {missing:,.0f} more in products to checkout.'
        )
    # Lock in a deterministic product order, matching POS stock locks.
    for product_id, needed in sorted(by_product.items()):
        free = free_available(product_id, lock=True)
        if needed > free:
            product = offers[next(k for k in offer_ids if offers[k].product_id == product_id)].product
            raise ValueError(f'Only {free} {product.name} units are available right now.')
    order = OnlineOrder.objects.create(
        account=account, customer_name=name, customer_phone=phone, address=address,
        zone=zone, note=note, subtotal=subtotal,
        delivery_fee=cash(zone.delivery_fee), total=cash(subtotal + zone.delivery_fee),
        reserved_until=timezone.now() + timedelta(minutes=20),
    )
    order.order_number = f'RNWEB-{order.id:06d}'
    order.save(update_fields=['order_number'])
    OnlineOrderItem.objects.bulk_create([
        OnlineOrderItem(order=order, offer=offer, product=offer.product,
            product_name=offer.product.name, pack_size=offer.pack_size,
            pack_count=count, egg_quantity=eggs, pack_price=offer.price_per_pack,
            line_total=line_total)
        for offer, count, eggs, line_total in clean
    ])
    OnlineOrderEvent.objects.create(order=order, event='placed', new_status=order.status,
        details='Public guest/retail checkout; 20-minute stock reservation')
    return order


@transaction.atomic
def action_order(order_id, actor, action, *, delivery_method='', rider_name='', reference='', actual_cost='0', reason='', customer_delivery_fee=None, source_allocations=None):
    order = OnlineOrder.objects.select_for_update().select_related('zone').get(pk=order_id)
    # Idempotent delivery: a repeated click/request must NEVER allocate FIFO eggs or
    # post another POS invoice/journal. The OneToOneField is a DB-level safeguard;
    # this row lock serializes simultaneous delivery requests on PostgreSQL.
    if action == 'deliver' and order.status == OnlineOrder.DELIVERED and order.pos_sale_id:
        return order
    allowed = {
        'confirm': [OnlineOrder.RESERVED],
        'pack': [OnlineOrder.CONFIRMED],
        'repack': [OnlineOrder.PACKED, OnlineOrder.DISPATCHED],
        'dispatch': [OnlineOrder.PACKED],
        'deliver': [OnlineOrder.DISPATCHED],
        'cancel': [OnlineOrder.RESERVED, OnlineOrder.CONFIRMED, OnlineOrder.PACKED],
        'settle': [OnlineOrder.DELIVERED],
        'change_fee': [OnlineOrder.RESERVED, OnlineOrder.CONFIRMED, OnlineOrder.PACKED, OnlineOrder.DISPATCHED],
    }
    if action not in allowed or order.status not in allowed[action]:
        raise ValueError('This action is not permitted for the current order status.')
    previous_status = order.status
    if action == 'change_fee':
        # Pre-invoice adjustment only. Never change a finalized POS invoice here.
        if order.pos_sale_id:
            raise ValueError('A final POS invoice already exists. Do not change the fee here.')
        if len(reason.strip()) < 3:
            raise ValueError('Provide a reason, for example Customer pickup or Free delivery.')
        try:
            fee = Decimal(str(customer_delivery_fee))
            if not fee.is_finite() or fee < 0 or fee > Decimal('100000.00'):
                raise ValueError('Customer delivery charge must be between Rs 0 and Rs 100,000.')
            fee = cash(fee)
        except (ArithmeticError, ValueError, TypeError):
            raise ValueError('Enter a valid customer delivery charge, such as 0 or 250.')
        old_fee = cash(order.delivery_fee)
        if fee == old_fee:
            raise ValueError('The new delivery fee matches the existing fee.')
        order.delivery_fee = fee
        order.total = cash(order.subtotal + fee)
        order.save(update_fields=['delivery_fee', 'total', 'updated_at'])
        OnlineOrderEvent.objects.create(order=order, actor=actor, event='change_fee',
            previous_status=previous_status, new_status=order.status,
            details=f'Customer delivery fee Rs {old_fee:.2f} → Rs {fee:.2f}; reason: {reason.strip()[:240]}')
        return order
    if action == 'confirm':
        by_product = defaultdict(int)
        for item in order.items.all():
            by_product[primary_id(item.product_id)] += item.egg_quantity
        # A timed-out reservation must be acquired again under stock locks.
        for product_id, qty in sorted(by_product.items()):
            free = free_available(product_id, lock=True, excluding_order=order.id)
            if qty > free:
                raise ValueError(f'Only {free} eggs are free for product #{product_id}; cannot confirm.')
        order.status = OnlineOrder.CONFIRMED
    elif action in ('pack', 'repack'):
        # Allocate specific physical source SKUs at packing. No stock is deducted
        # yet: these SKU choices are reserved until delivery/cancellation.
        source_allocations = source_allocations or {}
        item_rows = list(order.items.select_related('product').order_by('id'))
        aggregated = defaultdict(int)
        clean_rows = {}
        for item in item_rows:
            sources = list(source_ids(item.product_id))
            entered = source_allocations.get(item.pk)
            if entered is None and len(sources) == 1:
                # Existing single-SKU orders keep their one-click packing flow.
                entered = {sources[0]: item.egg_quantity}
            if entered is None:
                raise ValueError(f'Allocate the farm/outside source mix for {item.product_name} before packing.')
            try:
                clean = {int(k): int(v) for k, v in entered.items() if int(v) != 0}
            except (TypeError, ValueError, AttributeError):
                raise ValueError('Source quantities must be whole numbers.')
            if any(v <= 0 for v in clean.values()) or sum(clean.values()) != item.egg_quantity:
                raise ValueError(f'{item.product_name}: source quantities must total {item.egg_quantity} eggs.')
            if set(clean) - set(sources):
                raise ValueError('A selected source SKU is not part of this product.')
            clean_rows[item.pk] = clean
            for source_id, qty in clean.items():
                aggregated[source_id] += qty
        # Lock all eligible physical lots BEFORE checking other packed holds.
        source_stock = {}
        for source_id in sorted(aggregated):
            source_stock[source_id] = source_available(source_id, lock=True)
        for source_id, qty in aggregated.items():
            free = source_stock[source_id] - packed_source_reserved(source_id, excluding_order=order.pk)
            if qty > free:
                source = EggPOSProduct.objects.get(pk=source_id)
                raise ValueError(f'{source.sku}: only {max(free,0)} eggs available for packing; {qty} requested.')
        # Do not let a large order consume stock held for other online customers.
        for product_id in sorted({primary_id(item.product_id) for item in item_rows}):
            needed = sum(item.egg_quantity for item in item_rows if primary_id(item.product_id) == product_id)
            if needed > free_available(product_id, lock=True, excluding_order=order.pk):
                raise ValueError('The total unallocated stock is reserved for another order.')
        OnlineOrderItemSource.objects.filter(item__order=order).delete()
        OnlineOrderItemSource.objects.bulk_create([
            OnlineOrderItemSource(item_id=item_id, source_product_id=source_id, quantity=qty)
            for item_id, mix in clean_rows.items() for source_id, qty in mix.items()
        ])
        if action == 'pack':
            order.status = OnlineOrder.PACKED
        OnlineOrderEvent.objects.create(order=order, actor=actor, event='source_allocation',
            previous_status=previous_status, new_status=order.status,
            details=('; '.join(
                f'{item.pk}: ' + ', '.join(f'{EggPOSProduct.objects.get(pk=k).sku}={v}' for k,v in sorted(clean_rows[item.pk].items()))
                for item in item_rows
            ) + (f'; reason: {reason.strip()[:100]}' if reason else ''))[:500])
    elif action == 'dispatch':
        if delivery_method not in ('own','third'):
            raise ValueError('Select our rider or a third-party courier.')
        if delivery_method == 'own' and not order.zone.own_rider_allowed:
            raise ValueError('Our riders do not serve this area.')
        if delivery_method == 'third' and not order.zone.third_party_allowed:
            raise ValueError('Third-party delivery is unavailable in this area.')
        if not rider_name.strip():
            raise ValueError('Enter the rider name or courier company.')
        try:
            cost = cash(actual_cost)
        except (TypeError, ValueError, ArithmeticError):
            raise ValueError('Enter a valid delivery cost.')
        if cost < 0:
            raise ValueError('Delivery cost cannot be negative.')
        order.delivery_method = delivery_method
        order.rider_name = rider_name.strip()[:120]
        order.tracking_reference = reference.strip()[:120]
        order.delivery_cost = cost
        order.status = OnlineOrder.DISPATCHED
    elif action == 'deliver':
        if order.pos_sale_id:
            raise ValueError('This order already has a POS invoice.')
        # Lock lots first; remove this reservation atomically as sale is posted.
        by_product = defaultdict(int)
        for item in order.items.select_related('product').all():
            by_product[primary_id(item.product_id)] += item.egg_quantity
        for product_id, qty in sorted(by_product.items()):
            free = free_available(product_id, lock=True, excluding_order=order.id)
            if qty > free:
                raise ValueError(f'Stock changed for product #{product_id}; cannot complete delivery.')
        # Sale total includes charged delivery, while its journal separates delivery income.
        sale = EggPOSSale.objects.create(
            sale_date=timezone.localdate(), customer_name=order.customer_name,
            customer_phone=order.customer_phone, payment_method='credit',
            subtotal=order.subtotal, net_total=order.total,
            notes=f'Original online order {order.order_number} · COD · {order.zone.name} · {order.address[:100]}',
            created_by=actor,
        )
        sale.sale_number = f'RNE-{sale.pk:05d}'
        sale.save(update_fields=['sale_number'])
        cogs = Decimal('0.00')
        for item in order.items.all():
            pos_item = EggPOSSaleItem.objects.create(
                sale=sale, product=item.product, quantity=item.egg_quantity,
                sale_unit=('egg' if item.product.is_egg else 'jar'), unit_count=item.egg_quantity, unit_size=1,
                unit_rate=cash(item.line_total / item.egg_quantity),
                unit_price=cash(item.line_total / item.egg_quantity),
                line_total=item.line_total,
            )
            selections = {a.source_product_id: a.quantity for a in item.source_allocations.all()}
            if selections:
                cogs += allocate_selected_sources(pos_item, sale.sale_date, selections,
                                                  excluding_online_order=order.pk)
            elif len(source_ids(item.product_id)) == 1:
                # Legacy dispatched orders from before source-allocation migration.
                cogs += allocate_fifo_to_sale_item(pos_item, sale.sale_date)
            else:
                raise ValueError('This mixed-SKU order needs an audited source allocation before delivery.')
        sale.cogs_total = cash(cogs)
        sale.profit_total = cash(sale.net_total - sale.cogs_total)
        sale.save(update_fields=['cogs_total','profit_total'])
        order.pos_sale = sale
        order.status = OnlineOrder.DELIVERED
        order.save(update_fields=['pos_sale', 'status', 'updated_at'])
        sync_sale_invoice(sale)
        OnlineOrderEvent.objects.create(order=order, actor=actor, event=action,
            previous_status=previous_status, new_status=order.status,
            details=f'Created POS invoice {sale.sale_number}')
        return order
    elif action == 'cancel':
        if not reason.strip():
            raise ValueError('Enter why the order was cancelled.')
        order.status = OnlineOrder.CANCELLED
        order.cancelled_reason = reason.strip()[:255]
    elif action == 'settle':
        if not order.pos_sale_id or order.cod_settled_at:
            raise ValueError('This order is already settled or has no POS invoice.')
        # Also guard against cash already recorded through the existing POS screen.
        previous = cash(sum((p.amount for p in order.pos_sale.payments.all()), Decimal('0')))
        if previous:
            if previous == order.total:
                order.cod_settled_at = timezone.now()
                order.save(update_fields=['cod_settled_at', 'updated_at'])
                OnlineOrderEvent.objects.create(order=order, actor=actor, event='settle',
                    previous_status=previous_status, new_status=order.status,
                    details='Existing POS payment reconciled without creating a duplicate')
                return order
            raise ValueError('A partial payment already exists in POS. Reconcile it there before settling this order.')
        # This is a company cash receipt, not a rider receipt. Record only upon handover.
        payment = EggPOSSalePayment.objects.create(
            sale=order.pos_sale, payment_date=timezone.localdate(),
            amount=order.total, payment_method='cash',
            reference=f'{order.order_number}-COD',
            notes='COD handed over to company cashier', recorded_by=actor,
        )
        sync_sale_payment(payment)
        order.cod_settled_at = timezone.now()
    order.save()
    OnlineOrderEvent.objects.create(order=order, actor=actor, event=action,
        previous_status=previous_status, new_status=order.status,
        details=(order.cancelled_reason if action == 'cancel' else
            ('COD handed to company cashier' if action == 'settle' else
             (f'{order.delivery_method} · {order.rider_name}' if action == 'dispatch' else ''))))
    return order
