"""Public RayNoor Lahore shop + separate admin-only fulfillment dashboard."""
import re
from django.views.decorators.cache import never_cache
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.hashers import make_password, check_password
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.db import IntegrityError
from django.http import Http404, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST, require_http_methods, require_GET
from api.models.online_shop import OnlineRetailAccount, OnlineOffer, OnlineDeliveryZone, OnlineOrder
from api.forms_online_shop import OnlineOfferSetupForm, OnlineDeliveryZoneSetupForm
from api.models.egg_pos import EggPOSProduct
from api.services.online_shop import checkout_order, action_order, free_available, cash


PHONE = re.compile(r'^03\d{9}$')


def _account(request):
    pk = request.session.get('raynoor_retail_account')
    return OnlineRetailAccount.objects.filter(pk=pk).first() if pk else None


def _context(request):
    cart = request.session.get('raynoor_cart', {})
    return {'account': _account(request), 'cart_count': sum(int(n) for n in cart.values())}


def _clean_phone(raw):
    value = re.sub(r'[\s\-()]', '', raw or '')
    if value.startswith('+92'):
        value = '0' + value[3:]
    if value.startswith('92'):
        value = '0' + value[2:]
    if not PHONE.fullmatch(value):
        raise ValueError('Enter a valid Pakistani mobile number (03XXXXXXXXX).')
    return value


def _limit(request, category, limit, window=3600):
    remote = (request.META.get('REMOTE_ADDR') or 'unknown')[:64]
    key = f'raynoor:{category}:{remote}'
    count = cache.get(key, 0)
    if count >= limit:
        raise ValueError('Too many attempts. Please try again later.')
    cache.set(key, count+1, window)


@require_GET
def offer_photo(request, offer_id):
    """Serve sanitized photo bytes from DB; unpublished offers visible only to shop admins."""
    offer = get_object_or_404(OnlineOffer.objects.select_related('product'), pk=offer_id)
    public = offer.is_active and offer.product.is_active
    if not public and not _can_manage_shop_settings(request.user):
        raise Http404('Photo not found')
    if not offer.photo_mime or not offer.photo_data:
        raise Http404('Photo not found')
    response = HttpResponse(bytes(offer.photo_data), content_type='image/jpeg')
    response['X-Content-Type-Options'] = 'nosniff'
    response['Content-Disposition'] = 'inline; filename="raynoor-egg-pack.jpg"'
    response['Cache-Control'] = 'public, max-age=300' if public else 'private, no-store'
    return response


def storefront(request):
    offers = list(OnlineOffer.objects.filter(is_active=True, product__is_active=True)
                  .select_related('product').defer('photo_data').order_by('sort_order', 'id'))
    products = []
    for offer in offers:
        available = free_available(offer.product_id)
        products.append({'offer': offer, 'stock_packs': min(24, available // offer.pack_size)})
    return render(request, 'api/online_storefront.html', {
        **_context(request), 'products': products, 'zones_active': OnlineDeliveryZone.objects.filter(is_active=True).exists(),
    })


@require_POST
def cart_change(request):
    cart = dict(request.session.get('raynoor_cart', {}))
    try:
        offer_id = int(request.POST.get('offer_id', ''))
        amount = int(request.POST.get('count', '1'))
        if amount < 0 or amount > 24:
            raise ValueError('Choose between 0 and 24 packs.')
        if amount and not OnlineOffer.objects.filter(pk=offer_id, is_active=True, product__is_active=True).exists():
            raise ValueError('That product is not available.')
        if amount:
            cart[str(offer_id)] = amount
        else:
            cart.pop(str(offer_id), None)
        if len(cart) > 20 or sum(cart.values()) > 60:
            raise ValueError('Please contact us for bulk orders.')
        request.session['raynoor_cart'] = cart
        request.session.modified = True
    except (ValueError, TypeError) as error:
        messages.error(request, str(error))
    return redirect('raynoor_cart')


def _cart_rows(request):
    cart = request.session.get('raynoor_cart', {})
    offered = {str(o.pk): o for o in OnlineOffer.objects.filter(pk__in=cart, is_active=True, product__is_active=True).select_related('product').defer('photo_data')}
    rows = []
    total = Decimal('0')
    for offer_id, count in cart.items():
        offer = offered.get(offer_id)
        if offer and 0 < int(count) <= 24:
            amount = cash(offer.price_per_pack * int(count))
            rows.append({'offer':offer, 'count':int(count), 'amount':amount, 'eggs':offer.pack_size*int(count)})
            total += amount
    return rows, cash(total)


def cart(request):
    rows, subtotal = _cart_rows(request)
    return render(request, 'api/online_cart.html', {**_context(request), 'rows':rows, 'subtotal':subtotal})


@require_http_methods(['GET','POST'])
def checkout(request):
    rows, subtotal = _cart_rows(request)
    if not rows:
        messages.info(request, 'Your cart is empty.')
        return redirect('raynoor_store')
    zones = OnlineDeliveryZone.objects.filter(is_active=True)
    account = _account(request)
    if request.method == 'POST':
        try:
            _limit(request, 'checkout', 15, 3600)
            name = (request.POST.get('name') or '').strip()
            phone = _clean_phone(request.POST.get('phone'))
            address = (request.POST.get('address') or '').strip()
            note = (request.POST.get('note') or '').strip()
            if not (2 <= len(name) <= 120):
                raise ValueError('Enter your full name.')
            if not (10 <= len(address) <= 500):
                raise ValueError('Enter a complete Lahore delivery address.')
            if len(note) > 500:
                raise ValueError('Order note is too long.')
            try:
                zone_id = int(request.POST.get('zone_id') or '')
            except (TypeError, ValueError):
                raise ValueError('Select a valid Lahore delivery area.')
            zone = zones.filter(pk=zone_id).first()
            if zone is None:
                raise ValueError('Select an available Lahore delivery area.')
            if account and account.phone != phone:
                raise ValueError('Use your account phone number, or sign out to order as a guest.')
            order = checkout_order(request.session.get('raynoor_cart', {}), account=account,
                name=name, phone=phone, address=address, zone=zone, note=note)
            request.session['raynoor_cart'] = {}
            _remember_guest_order(request, order.token)
            request.session.modified = True
            return redirect('raynoor_order_tracking', token=order.token)
        except (ValueError, TypeError) as error:
            messages.error(request, str(error))
    return render(request, 'api/online_checkout.html', {
        **_context(request), 'rows':rows, 'subtotal':subtotal, 'zones':zones,
    })


@require_http_methods(['GET'])
def order_document(request, token):
    """Shareable, print-friendly document bound to the original online order.

    No sale, stock allocation, payment, or journal is created by opening/printing.
    An unguessable order token grants read-only access; avoid sharing it publicly.
    """
    order = get_object_or_404(
        OnlineOrder.objects.select_related('zone', 'pos_sale').prefetch_related('items'),
        token=token,
    )
    is_final = order.status == OnlineOrder.DELIVERED and bool(order.pos_sale_id)
    paid = cash(order.pos_sale.amount_paid) if is_final else Decimal('0.00')
    balance = max(Decimal('0.00'), cash(order.total - paid))
    response = render(request, 'api/online_order_document.html', {
        'order': order, 'is_final': is_final, 'amount_paid': paid,
        'balance_due': balance,
    })
    response['Cache-Control'] = 'private, no-store'
    response['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
    response['Referrer-Policy'] = 'no-referrer'
    return response


# Remember up to five private tracking links in the current browser session.
# This is convenience only: guest access never depends on login/session state.
GUEST_RECENT_ORDERS_KEY = 'raynoor_recent_tracking_tokens'


def _remember_guest_order(request, token):
    tokens = request.session.get(GUEST_RECENT_ORDERS_KEY, [])
    if not isinstance(tokens, list):
        tokens = []
    request.session[GUEST_RECENT_ORDERS_KEY] = [token] + [t for t in tokens if t != token and isinstance(t, str)][:4]
    request.session.modified = True


def _recent_guest_orders(request):
    tokens = request.session.get(GUEST_RECENT_ORDERS_KEY, [])
    if not isinstance(tokens, list):
        return []
    return list(OnlineOrder.objects.filter(token__in=tokens[:5]).only(
        'order_number', 'token', 'status', 'updated_at'
    ).order_by('-created_at')[:5])


def _order_status_context(order):
    expired = bool(order.status == OnlineOrder.RESERVED and order.reserved_until and order.reserved_until <= timezone.now())
    names = ['Order placed', 'Confirmed', 'Packed', 'Out for delivery', 'Delivered']
    indexes = {
        OnlineOrder.RESERVED: 0,
        OnlineOrder.CONFIRMED: 1,
        OnlineOrder.PACKED: 2,
        OnlineOrder.DISPATCHED: 3,
        OnlineOrder.DELIVERED: 4,
    }
    index = indexes.get(order.status, 0)
    cancelled = order.status in (OnlineOrder.CANCELLED, OnlineOrder.EXPIRED) or expired
    descriptions = {
        OnlineOrder.RESERVED: 'We have received your order and are checking availability. We will confirm it shortly.',
        OnlineOrder.CONFIRMED: 'Your order is confirmed. We are preparing your eggs for packing.',
        OnlineOrder.PACKED: 'Your eggs are packed and ready for delivery.',
        OnlineOrder.DISPATCHED: 'Your order is on its way to your Lahore address.',
        OnlineOrder.DELIVERED: 'Your order has been delivered. Thank you for choosing RayNoor.',
    }
    status_label = ('Reservation expired' if expired else order.get_status_display())
    status_note = ('This order is no longer active. Please contact RayNoor before placing a replacement order.'
                   if cancelled else descriptions.get(order.status, 'Check back soon for updates.'))
    steps = []
    for i, name in enumerate(names):
        stage = 'pending' if cancelled else ('done' if i < index else ('current' if i == index else 'pending'))
        if cancelled and i == 0:
            stage = 'done'
        steps.append({'number': i+1, 'label': name, 'state': stage, 'is_current': stage == 'current'})
    return {'expired': expired, 'is_stopped': cancelled, 'status_label': status_label,
            'status_note': status_note, 'progress_steps': steps}


@never_cache
def tracking(request, token):
    order = get_object_or_404(OnlineOrder.objects.select_related('zone').prefetch_related('items'), token=token)
    _remember_guest_order(request, order.token)
    response = render(request, 'api/online_tracking.html', {
        **_context(request), 'order': order, **_order_status_context(order),
        'tracking_link': request.build_absolute_uri(reverse('raynoor_order_tracking', kwargs={'token': order.token})),
    })
    response['Cache-Control'] = 'private, no-store'
    response['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
    response['Referrer-Policy'] = 'no-referrer'
    return response


@require_http_methods(['GET', 'POST'])
@never_cache
def lookup_order_status(request):
    """Public status-only lookup; never reveals a private invoice/tracking token.

    Customers who retained the private tracking URL can see full order details.
    Customers who lost it can check status with order reference and matching
    checkout phone without login or SMS charges.
    """
    order = None
    lookup_error = ''
    if request.method == 'POST':
        try:
            # Throttle even invalid formats and nonexistent references.
            _limit(request, 'guest_status_lookup', 8, 3600)
            number = (request.POST.get('order_number') or '').strip().upper()
            phone = _clean_phone(request.POST.get('phone'))
            if not re.fullmatch(r'RNWEB-\d{6,12}', number):
                raise ValueError('Invalid reference')
            order = OnlineOrder.objects.filter(order_number=number, customer_phone=phone).only(
                'order_number', 'status', 'updated_at', 'reserved_until'
            ).first()
            if order is None:
                raise ValueError('Order not found')
        except ValueError as error:
            # Generic response prevents enumeration of order numbers and phones.
            lookup_error = ('Too many attempts. Please try again later.' if 'Too many attempts' in str(error)
                            else 'No matching order found. Check the order number and checkout phone.')
    response = render(request, 'api/online_order_lookup.html', {
        **_context(request), 'lookup_order': order, 'lookup_error': lookup_error,
        'status_context': _order_status_context(order) if order else None,
        'recent_orders': _recent_guest_orders(request),
    })
    response['Cache-Control'] = 'private, no-store'
    response['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
    response['Referrer-Policy'] = 'no-referrer'
    return response


@require_http_methods(['GET','POST'])
def retail_register(request):
    if _account(request):
        return redirect('raynoor_account')
    if request.method == 'POST':
        try:
            _limit(request, 'register', 5, 86400)
            name = (request.POST.get('name') or '').strip()
            phone = _clean_phone(request.POST.get('phone'))
            password = request.POST.get('password') or ''
            if len(name) < 2 or len(name) > 120:
                raise ValueError('Enter your name.')
            validate_password(password)
            account = OnlineRetailAccount.objects.create(
                name=name, phone=phone, password_hash=make_password(password))
            request.session.cycle_key()
            request.session['raynoor_retail_account'] = account.pk
            return redirect('raynoor_account')
        except (ValueError, IntegrityError) as error:
            messages.error(request, 'Unable to register. Check your details or use sign-in.' if isinstance(error, IntegrityError) else str(error))
        except Exception as error:
            from django.core.exceptions import ValidationError
            if isinstance(error, ValidationError):
                messages.error(request, ' '.join(error.messages))
            else:
                raise
    return render(request, 'api/online_account_login.html', {**_context(request), 'register':True})


@require_http_methods(['GET','POST'])
def retail_login(request):
    if _account(request):
        return redirect('raynoor_account')
    if request.method == 'POST':
        try:
            _limit(request, 'login', 12, 3600)
            phone = _clean_phone(request.POST.get('phone'))
            password = request.POST.get('password') or ''
            account = OnlineRetailAccount.objects.filter(phone=phone).first()
            if not account or not check_password(password, account.password_hash):
                raise ValueError('Incorrect phone or password.')
            request.session.cycle_key()
            request.session['raynoor_retail_account'] = account.pk
            return redirect('raynoor_account')
        except ValueError as error:
            messages.error(request, str(error))
    return render(request, 'api/online_account_login.html', {**_context(request), 'register':False})


@require_POST
def retail_logout(request):
    request.session.pop('raynoor_retail_account', None)
    return redirect('raynoor_store')


def account_orders(request):
    account = _account(request)
    if account is None:
        return redirect('raynoor_retail_login')
    orders = OnlineOrder.objects.filter(account=account).order_by('-created_at')[:50]
    return render(request, 'api/online_account_orders.html', {**_context(request), 'orders':orders})


def _require_admin(request):
    if not request.user.is_authenticated or not (request.user.is_staff or request.user.is_superuser):
        raise Http404('Not found')


def _can_manage_shop_settings(user):
    """Only owner/superuser or a Django admin explicitly granted both model permissions."""
    return bool(user.is_authenticated and user.is_active and (
        user.is_superuser or (
            user.is_staff and user.has_perms((
                'api.add_onlineoffer', 'api.change_onlineoffer',
                'api.add_onlinedeliveryzone', 'api.change_onlinedeliveryzone',
            ))
        )
    ))


@require_http_methods(['GET', 'POST'])
def shop_setup(request):
    """A discoverable product/area setup screen inside Egg POS; no migration needed."""
    if not _can_manage_shop_settings(request.user):
        raise Http404('Not found')

    offers = OnlineOffer.objects.select_related('product').defer('photo_data').order_by('sort_order', 'id')
    zones = OnlineDeliveryZone.objects.all().order_by('name')
    try:
        offer_id = int(request.POST.get('object_id') or 0) if request.method == 'POST' and request.POST.get('kind') == 'offer' else int(request.GET.get('offer') or 0)
        zone_id = int(request.POST.get('object_id') or 0) if request.method == 'POST' and request.POST.get('kind') == 'zone' else int(request.GET.get('zone') or 0)
    except (TypeError, ValueError):
        raise Http404('Invalid configuration item')
    offer_instance = get_object_or_404(OnlineOffer, pk=offer_id) if offer_id else None
    zone_instance = get_object_or_404(OnlineDeliveryZone, pk=zone_id) if zone_id else None
    offer_form = OnlineOfferSetupForm(instance=offer_instance)
    zone_form = OnlineDeliveryZoneSetupForm(instance=zone_instance)

    if request.method == 'POST':
        kind = request.POST.get('kind')
        if kind == 'offer':
            offer_form = OnlineOfferSetupForm(request.POST, request.FILES, instance=offer_instance)
            if offer_form.is_valid():
                offer_form.save()
                messages.success(request, 'Retail egg pack saved. Active packs appear in the shop automatically.')
                return redirect('raynoor_shop_setup')
        elif kind == 'zone':
            zone_form = OnlineDeliveryZoneSetupForm(request.POST, instance=zone_instance)
            if zone_form.is_valid():
                zone_form.save()
                messages.success(request, 'Lahore delivery area saved.')
                return redirect('raynoor_shop_setup')
        else:
            raise Http404('Unknown setup action')

    def field_value(kind, name, fallback=''):
        if request.method == 'POST' and request.POST.get('kind') == kind:
            return (request.POST.get(name) or '')
        target = offer_instance if kind == 'offer' else zone_instance
        if target is None:
            return fallback
        return str(getattr(target, name, fallback) or fallback)

    def checkbox_value(kind, name, default=False):
        if request.method == 'POST' and request.POST.get('kind') == kind:
            return name in request.POST
        target = offer_instance if kind == 'offer' else zone_instance
        return bool(getattr(target, name, default))

    offer_values = {
        'product': field_value('offer', 'product_id'),
        'pack_size': field_value('offer', 'pack_size', '12'),
        'title': field_value('offer', 'title'),
        'price_per_pack': field_value('offer', 'price_per_pack'),
        'photo_fit': field_value('offer', 'photo_fit', 'contain'),
        'photo_position': field_value('offer', 'photo_position', 'center'),
        'photo_alt': field_value('offer', 'photo_alt'),
        'sort_order': field_value('offer', 'sort_order', '0'),
        'is_active': checkbox_value('offer', 'is_active'),
    }
    zone_values = {
        'name': field_value('zone', 'name'),
        'delivery_fee': field_value('zone', 'delivery_fee', '0'),
        'minimum_order': field_value('zone', 'minimum_order', '0'),
        'own_rider_allowed': checkbox_value('zone', 'own_rider_allowed', True),
        'third_party_allowed': checkbox_value('zone', 'third_party_allowed', True),
        'is_active': checkbox_value('zone', 'is_active'),
    }
    return render(request, 'api/online_shop_setup.html', {
        'offer_values': offer_values, 'zone_values': zone_values,
        'products': EggPOSProduct.objects.all().order_by('name'),
        'offers': offers, 'zones': zones, 'offer_form': offer_form, 'zone_form': zone_form,
        'edit_offer': offer_instance, 'edit_zone': zone_instance,
        'product_count': EggPOSProduct.objects.filter(is_active=True).count(),
        'active_offer_count': offers.filter(is_active=True, product__is_active=True).count(),
        'active_zone_count': zones.filter(is_active=True).count(),
    })


def admin_orders(request):
    _require_admin(request)
    orders = OnlineOrder.objects.select_related('zone','pos_sale').order_by('-created_at')[:150]
    return render(request, 'api/online_admin_orders.html', {'orders':orders})


def admin_order_detail(request, order_id):
    _require_admin(request)
    order = get_object_or_404(OnlineOrder.objects.select_related('zone','pos_sale').prefetch_related('items'), pk=order_id)
    return render(request, 'api/online_admin_order_detail.html', {'order':order})


@require_POST
def admin_order_action(request, order_id):
    _require_admin(request)
    try:
        if request.POST.get('action') == 'settle' and not request.user.check_password(request.POST.get('admin_password') or ''):
            raise ValueError('Enter your correct admin password before recording COD cash received.')
        action_order(order_id, request.user, request.POST.get('action') or '',
            delivery_method=request.POST.get('delivery_method') or '',
            rider_name=request.POST.get('rider_name') or '',
            reference=request.POST.get('tracking_reference') or '',
            actual_cost=request.POST.get('delivery_cost') or '0',
            reason=request.POST.get('reason') or '')
        messages.success(request, 'Order updated successfully.')
    except (ValueError, ArithmeticError) as error:
        messages.error(request, str(error))
    return redirect('raynoor_admin_order_detail', order_id=order_id)
