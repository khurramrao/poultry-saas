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
from api.models.online_shop import OnlineOffer, OnlineOrder, OnlineOrderItem, OnlineOrderEvent
from api.services.egg_pos import allocate_fifo_to_sale_item
from api.services.egg_pos_accounting import sync_sale_invoice, sync_sale_payment

CENT = Decimal('0.01')
# Egg merchandise subtotal, before customer delivery fees or courier costs.
MIN_EGGS_SUBTOTAL = Decimal('1000.00')


def cash(value):
    return Decimal(value).quantize(CENT)


def reserved_quantity(product_id, excluding_order=None):
    now = timezone.now()
    qs = OnlineOrderItem.objects.filter(product_id=product_id).filter(
        Q(order__status__in=[OnlineOrder.CONFIRMED, OnlineOrder.PACKED, OnlineOrder.DISPATCHED])
        | Q(order__status=OnlineOrder.RESERVED, order__reserved_until__gt=now)
    )
    if excluding_order:
        qs = qs.exclude(order_id=excluding_order)
    return int(qs.aggregate(s=Sum('egg_quantity'))['s'] or 0)


def gross_available(product_id, as_of=None, lock=False):
    as_of = as_of or timezone.localdate()
    lots = EggPOSInventoryLot.objects.filter(
        product_id=product_id, quantity_remaining__gt=0, received_date__lte=as_of,
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
        raise ValueError('Add at least one egg product to your cart.')
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
        eggs = int(offer.pack_size) * count
        line_total = cash(offer.price_per_pack * count)
        by_product[offer.product_id] += eggs
        subtotal += line_total
        clean.append((offer, count, eggs, line_total))
    subtotal = cash(subtotal)
    # Apply both the RayNoor-wide floor and any higher area-specific minimum.
    # Do not count the delivery fee toward the egg merchandise minimum.
    minimum = max(MIN_EGGS_SUBTOTAL, cash(zone.minimum_order))
    if subtotal < minimum:
        missing = cash(minimum - subtotal)
        raise ValueError(
            f'Minimum egg order for {zone.name} is Rs {minimum:,.0f} before delivery. '
            f'Add Rs {missing:,.0f} more in egg packs to checkout.'
        )
    # Lock in a deterministic product order, matching POS stock locks.
    for product_id, needed in sorted(by_product.items()):
        free = free_available(product_id, lock=True)
        if needed > free:
            product = offers[next(k for k in offer_ids if offers[k].product_id == product_id)].product
            raise ValueError(f'Only {free} {product.name} eggs are available right now.')
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
def action_order(order_id, actor, action, *, delivery_method='', rider_name='', reference='', actual_cost='0', reason='', customer_delivery_fee=None):
    order = OnlineOrder.objects.select_for_update().select_related('zone').get(pk=order_id)
    # Idempotent delivery: a repeated click/request must NEVER allocate FIFO eggs or
    # post another POS invoice/journal. The OneToOneField is a DB-level safeguard;
    # this row lock serializes simultaneous delivery requests on PostgreSQL.
    if action == 'deliver' and order.status == OnlineOrder.DELIVERED and order.pos_sale_id:
        return order
    allowed = {
        'confirm': [OnlineOrder.RESERVED],
        'pack': [OnlineOrder.CONFIRMED],
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
            by_product[item.product_id] += item.egg_quantity
        # A timed-out reservation must be acquired again under stock locks.
        for product_id, qty in sorted(by_product.items()):
            free = free_available(product_id, lock=True, excluding_order=order.id)
            if qty > free:
                raise ValueError(f'Only {free} eggs are free for product #{product_id}; cannot confirm.')
        order.status = OnlineOrder.CONFIRMED
    elif action == 'pack':
        order.status = OnlineOrder.PACKED
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
            by_product[item.product_id] += item.egg_quantity
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
                sale_unit='egg', unit_count=item.egg_quantity, unit_size=1,
                unit_rate=cash(item.line_total / item.egg_quantity),
                unit_price=cash(item.line_total / item.egg_quantity),
                line_total=item.line_total,
            )
            cogs += allocate_fifo_to_sale_item(pos_item, sale.sale_date)
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
