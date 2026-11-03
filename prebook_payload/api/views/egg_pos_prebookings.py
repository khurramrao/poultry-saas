"""Private, manual social pre-bookings. Never reserves stock or posts sales/accounts."""
import uuid
from datetime import date
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from api.models.egg_pos import EggPOSProduct
from api.models.egg_pos_prebooking import (
    CHANNELS, STATUSES, UNITS, UNIT_MULTIPLIER,
    EggPOSManualPrebooking as Booking,
    EggPOSManualPrebookingLine as Line,
    EggPOSManualPrebookingEvent as Event,
)
from api.services.egg_pos import can_manage


def _is_admin(user):
    return bool(user.is_authenticated and
                (user.is_superuser or (user.is_staff and can_manage(user))))


def _validate(data):
    name = (data.get("customer_name") or "").strip()
    phone = (data.get("customer_phone") or "").strip()
    channel = (data.get("channel") or "").strip()
    area = (data.get("delivery_area") or "").strip()
    notes = (data.get("notes") or "").strip()
    if not (2 <= len(name) <= 150):
        raise ValueError("Enter the customer's name (2–150 characters).")
    digits = ''.join(c for c in phone if c.isdigit())
    if not (10 <= len(digits) <= 15) or len(phone) > 40 or not all(c.isdigit() or c in "+ -()" for c in phone):
        raise ValueError("Enter a valid customer contact number (for example 03001234567).")
    if channel not in dict(CHANNELS):
        raise ValueError("Select where the pre-booking came from.")
    if len(area) > 180 or len(notes) > 2000:
        raise ValueError("Area or notes are too long.")
    expected_date = None
    if data.get("expected_date"):
        try:
            expected_date = date.fromisoformat(data["expected_date"])
        except (ValueError, TypeError):
            raise ValueError("Enter a valid expected delivery date.")
    lines = []
    total = 0
    for i in range(1, 6):
        product_raw = (data.get(f"product_{i}") or "").strip()
        quantity_raw = (data.get(f"quantity_{i}") or "").strip()
        unit = (data.get(f"unit_{i}") or "tray").strip()
        if not product_raw and not quantity_raw:
            continue
        if not product_raw or not quantity_raw:
            raise ValueError(f"Complete both product and quantity in item {i}.")
        try:
            product_id, quantity = int(product_raw), int(quantity_raw)
        except (ValueError, TypeError):
            raise ValueError(f"Item {i} must have a valid product and whole-number quantity.")
        if quantity < 1 or quantity > 10000:
            raise ValueError(f"Item {i} quantity must be between 1 and 10,000.")
        if unit not in UNIT_MULTIPLIER:
            raise ValueError(f"Invalid unit for item {i}.")
        if not EggPOSProduct.objects.filter(pk=product_id).exists():
            raise ValueError(f"Invalid egg product in item {i}.")
        lines.append((product_id, quantity, unit))
        total += quantity * UNIT_MULTIPLIER[unit]
    if not lines:
        raise ValueError("Add at least one egg product and quantity.")
    if total > 300000:
        raise ValueError("This booking is too large; record smaller bookings.")
    return dict(customer_name=name, customer_phone=phone, channel=channel,
                delivery_area=area, notes=notes, expected_date=expected_date), lines


def _log(booking, action, user, details=""):
    Event.objects.create(booking=booking, action=action, actor=user, details=details[:2000])


def _record(request):
    action = (request.POST.get("action") or "create").strip()
    if action == "create":
        fields, lines = _validate(request.POST)
        try:
            token = uuid.UUID(request.POST.get("request_token") or "")
        except (TypeError, ValueError, AttributeError):
            raise ValueError("The form expired. Please refresh and try again.")
        with transaction.atomic():
            old = Booking.objects.select_for_update().filter(request_token=token).first()
            if old:
                messages.info(request, f"{old.booking_number} was already saved; no duplicate created.")
                return
            booking, created = Booking.objects.get_or_create(
                request_token=token,
                defaults={**fields, "created_by": request.user},
            )
            if not created:
                messages.info(request, f"{booking.booking_number} was already saved.")
                return
            Line.objects.bulk_create([Line(booking=booking, product_id=pid,
                                           quantity=q, unit=unit) for pid, q, unit in lines])
            _log(booking, "created", request.user, "Manually entered social-channel pre-booking")
        messages.success(request, f"Saved {booking.booking_number}. No stock, sale or cash entry was created.")
        return

    try:
        booking_id = int(request.POST.get("booking_id") or 0)
    except (ValueError, TypeError):
        raise ValueError("Select a valid booking.")
    with transaction.atomic():
        booking = get_object_or_404(Booking.objects.select_for_update(), pk=booking_id)
        if action == "update":
            if booking.status not in ("awaiting_stock", "contact_customer"):
                raise ValueError("Only bookings not yet reconfirmed can be edited.")
            fields, lines = _validate(request.POST)
            previous = (f"Name: {booking.customer_name}; phone: {booking.customer_phone}; "
                        f"channel: {booking.channel}; items: " +
                        "; ".join(f"{l.product_id} x{l.quantity} {l.unit}" for l in booking.lines.all()))
            for field, value in fields.items():
                setattr(booking, field, value)
            booking.save(update_fields=[*fields, "updated_at"])
            booking.lines.all().delete()
            Line.objects.bulk_create([Line(booking=booking, product_id=pid,
                                           quantity=q, unit=unit) for pid, q, unit in lines])
            _log(booking, "edited", request.user, "Earlier details: " + previous)
            messages.success(request, f"Updated {booking.booking_number}; edit saved to audit history.")
        elif action == "stock_arrived":
            if booking.status != "awaiting_stock":
                raise ValueError("Only awaiting-stock bookings can be marked stock arrived.")
            # Manual signal only. Does not assume or modify physical stock.
            booking.status = "contact_customer"
            booking.stock_arrived_at = timezone.now()
            booking.save(update_fields=["status", "stock_arrived_at", "updated_at"])
            _log(booking, "stock_arrived", request.user, "Staff marked stock as arrived; customer reconfirmation pending")
            messages.success(request, "Stock-arrival note saved. Contact the customer before confirming.")
        elif action == "reconfirmed":
            if booking.status != "contact_customer":
                raise ValueError("Mark stock as arrived first; reconfirmation cannot be skipped.")
            if request.POST.get("customer_said_yes") != "yes":
                raise ValueError("Confirm that you contacted the customer and they accepted the order.")
            confirm_channel = (request.POST.get("reconfirmation_channel") or "").strip()
            if confirm_channel not in dict(CHANNELS):
                raise ValueError("Select how the customer reconfirmed.")
            booking.status = "confirmed"
            booking.reconfirmed_at = timezone.now()
            booking.reconfirmation_channel = confirm_channel
            booking.save(update_fields=["status", "reconfirmed_at", "reconfirmation_channel", "updated_at"])
            _log(booking, "reconfirmed", request.user, f"Customer accepted via {dict(CHANNELS)[confirm_channel]}")
            messages.success(request, "Customer reconfirmed. Enter the actual sale separately in Egg POS.")
        elif action == "completed":
            if booking.status != "confirmed":
                raise ValueError("Reconfirm with the customer before completion.")
            reference = (request.POST.get("invoice_reference") or "").strip()
            if not (2 <= len(reference) <= 80):
                raise ValueError("Enter the existing POS invoice/reference before marking completed.")
            booking.invoice_reference = reference
            booking.completed_at = timezone.now()
            booking.status = "completed"
            booking.save(update_fields=["status", "invoice_reference", "completed_at", "updated_at"])
            _log(booking, "completed", request.user, f"Actual POS invoice/reference entered manually: {reference}")
            messages.success(request, "Pre-booking marked completed. This action did not create another sale.")
        elif action == "cancelled":
            if booking.status in ("completed", "cancelled"):
                raise ValueError("Completed or already-cancelled bookings cannot be cancelled again.")
            reason = (request.POST.get("cancellation_reason") or "").strip()
            if len(reason) < 5 or len(reason) > 255:
                raise ValueError("Enter a meaningful cancellation/decline reason (5–255 characters).")
            booking.status = "cancelled"
            booking.cancellation_reason = reason
            booking.cancelled_at = timezone.now()
            booking.save(update_fields=["status", "cancellation_reason", "cancelled_at", "updated_at"])
            _log(booking, "cancelled", request.user, reason)
            messages.success(request, "Booking cancelled. No inventory or accounting records changed.")
        else:
            raise ValueError("Unknown action.")


def _context(request, edit_id=None):
    status = (request.GET.get("status") or "active").strip()
    search = (request.GET.get("q") or "").strip()[:80]
    qs = Booking.objects.prefetch_related("lines__product", "events__actor").select_related("created_by")
    if status == "active":
        qs = qs.exclude(status__in=["completed", "cancelled"])
    elif status in dict(STATUSES):
        qs = qs.filter(status=status)
    elif status != "all":
        status = "active"
        qs = qs.exclude(status__in=["completed", "cancelled"])
    if search:
        qs = qs.filter(Q(customer_name__icontains=search) | Q(customer_phone__icontains=search) |
                       Q(delivery_area__icontains=search))
    bookings = list(qs[:200])
    edit_booking = None
    if edit_id:
        edit_booking = get_object_or_404(Booking.objects.prefetch_related("lines"), pk=edit_id)
        if edit_booking.status not in ("awaiting_stock", "contact_customer"):
            edit_booking = None
    products = list(EggPOSProduct.objects.order_by("name", "id"))
    if request.method == 'POST' and request.POST.get('action') == 'update' and not edit_booking:
        try:
            edit_booking = get_object_or_404(Booking, pk=int(request.POST.get('booking_id') or 0))
        except (ValueError, TypeError):
            pass
    if request.method == 'POST' and request.POST.get('action') in ('create', 'update'):
        entry = request.POST
        lines = [dict(product_id=int(entry.get(f"product_{i}") or 0) if str(entry.get(f"product_{i}") or '0').isdigit() else 0,
                      quantity=entry.get(f"quantity_{i}") or "", unit=entry.get(f"unit_{i}") or "tray") for i in range(1,6)]
        form = dict(customer_name=entry.get('customer_name') or '',customer_phone=entry.get('customer_phone') or '',
                    channel=entry.get('channel') or '', delivery_area=entry.get('delivery_area') or '',
                    expected_date=entry.get('expected_date') or '',notes=entry.get('notes') or '')
        token = entry.get('request_token') or str(uuid.uuid4())
    elif edit_booking:
        form = dict(customer_name=edit_booking.customer_name, customer_phone=edit_booking.customer_phone,
                    channel=edit_booking.channel, delivery_area=edit_booking.delivery_area,
                    expected_date=edit_booking.expected_date.isoformat() if edit_booking.expected_date else '',
                    notes=edit_booking.notes)
        lines = [dict(product_id=l.product_id, quantity=l.quantity, unit=l.unit) for l in edit_booking.lines.all()]
        lines += [dict(product_id=0, quantity='',unit='tray')] * max(0, 5-len(lines))
        token = str(uuid.uuid4())
    else:
        form = dict(customer_name='', customer_phone='', channel='', delivery_area='', expected_date='', notes='')
        lines = [dict(product_id=0, quantity='', unit='tray') for _ in range(5)]
        token = str(uuid.uuid4())
    return dict(bookings=bookings, products=products, channels=CHANNELS, units=UNITS,
                statuses=STATUSES, active_status=status, search=search, today=timezone.localdate(),
                form=form, line_rows=lines, request_token=token, edit_booking=edit_booking,
                counts={s: Booking.objects.filter(status=s).count() for s, _ in STATUSES})


@login_required
@require_http_methods(['GET', 'POST'])
def manual_prebookings(request):
    if not _is_admin(request.user):
        return HttpResponseForbidden('Only authorized Egg POS administrators can manage manual pre-bookings.')
    if request.method == 'POST':
        try:
            _record(request)
        except (ValueError, ValidationError) as exc:
            messages.error(request, str(exc))
            if request.POST.get('action') in ('create', 'update'):
                return render(request, 'api/egg_pos_manual_prebookings.html', _context(request))
        else:
            return redirect('egg_pos_manual_prebookings')
    edit_id = request.GET.get('edit') or None
    return render(request, 'api/egg_pos_manual_prebookings.html', _context(request, edit_id=edit_id))
