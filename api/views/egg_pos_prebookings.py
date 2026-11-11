"""Private, manual social pre-bookings. Never reserves stock or posts sales/accounts."""
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.db.models import Sum
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from api.models.egg_pos import EggPOSProduct, EggPOSSale, EggPOSCustomer
from api.models.egg_pos_prebooking import (
    CHANNELS, STATUSES, UNITS, UNIT_MULTIPLIER,
    EggPOSManualPrebooking as Booking,
    EggPOSManualPrebookingLine as Line,
    EggPOSManualPrebookingEvent as Event,
)
from api.services.egg_pos import can_manage
from api.services.egg_pos_prebooking_whatsapp import build_prebooking_whatsapp


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
    # Require both ends of the time window, and a delivery date for scheduling.
    time_from_raw = (data.get("delivery_time_from") or "").strip()
    time_to_raw = (data.get("delivery_time_to") or "").strip()
    time_from = time_to = None
    if bool(time_from_raw) != bool(time_to_raw):
        raise ValueError("Enter both the delivery start and end time, or leave both blank.")
    if time_from_raw:
        if not expected_date:
            raise ValueError("Set the expected delivery date when entering a delivery time window.")
        try:
            time_from = datetime.strptime(time_from_raw, "%H:%M").time()
            time_to = datetime.strptime(time_to_raw, "%H:%M").time()
        except ValueError:
            raise ValueError("Use valid 24-hour times such as 15:00 and 17:00.")
        if time_from >= time_to:
            raise ValueError("Delivery end time must be later than the start time.")
    lines = []
    total = 0
    for i in range(1, 6):
        product_raw = (data.get(f"product_{i}") or "").strip()
        quantity_raw = (data.get(f"quantity_{i}") or "").strip()
        unit = (data.get(f"unit_{i}") or "tray").strip()
        rate_raw = (data.get(f"quoted_unit_price_{i}") or "").strip()
        if not product_raw and not quantity_raw and not rate_raw:
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
        unit_rate = None
        if rate_raw:
            try:
                unit_rate = Decimal(rate_raw)
            except InvalidOperation:
                raise ValueError(f"Enter a valid quoted rate for item {i}.")
            if (not unit_rate.is_finite() or unit_rate < 0 or unit_rate > Decimal("99999999.99")
                    or unit_rate.as_tuple().exponent < -2):
                raise ValueError(f"Item {i} rate must be a non-negative amount with at most two decimal places.")
        lines.append((product_id, quantity, unit, unit_rate))
        total += quantity * UNIT_MULTIPLIER[unit]
    if not lines:
        raise ValueError("Add at least one egg product and quantity.")
    if total > 300000:
        raise ValueError("This booking is too large; record smaller bookings.")
    return dict(customer_name=name, customer_phone=phone, channel=channel,
                delivery_area=area, notes=notes, expected_date=expected_date,
                delivery_time_from=time_from, delivery_time_to=time_to), lines


def _log(booking, action, user, details=""):
    Event.objects.create(booking=booking, action=action, actor=user, details=details[:2000])


def _linked_customer(request, existing=None):
    raw = (request.POST.get("customer_id") or "").strip()
    if not raw and existing is not None and existing.customer_id:
        raw = str(existing.customer_id)
    if not raw:
        return None
    try:
        customer_id = int(raw)
    except (TypeError, ValueError):
        raise ValueError("Invalid saved customer selection.")
    customer = EggPOSCustomer.objects.filter(pk=customer_id, is_active=True).first()
    if not customer:
        raise ValueError("Saved customer not found or is inactive.")
    if existing is not None and existing.customer_id and existing.customer_id != customer.id:
        raise ValueError("Cannot change the linked customer on an existing pre-booking.")
    if not customer.phone.strip():
        raise ValueError("Add the saved customer's phone number before recording this pre-booking.")
    return customer


def _record(request):
    action = (request.POST.get("action") or "create").strip()
    if action == "create":
        customer = _linked_customer(request)
        fields, lines = _validate(request.POST)
        if customer:
            fields.update(customer=customer, customer_name=customer.name, customer_phone=customer.phone)
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
                                           quantity=q, unit=unit, quoted_unit_price=rate)
                                           for pid, q, unit, rate in lines])
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
            customer = _linked_customer(request, existing=booking)
            fields, lines = _validate(request.POST)
            if customer:
                fields.update(customer=customer, customer_name=customer.name, customer_phone=customer.phone)
            previous = (f"Name: {booking.customer_name}; phone: {booking.customer_phone}; "
                        f"channel: {booking.channel}; date: {booking.expected_date}; "
                        f"time: {booking.delivery_window_display or 'not set'}; items: " +
                        "; ".join(f"{l.product_id} x{l.quantity} {l.unit} "
                                  f"@ {l.quoted_unit_price if l.quoted_unit_price is not None else 'unpriced'}"
                                  for l in booking.lines.all()))
            for field, value in fields.items():
                setattr(booking, field, value)
            booking.save(update_fields=[*fields, "updated_at"])
            booking.lines.all().delete()
            Line.objects.bulk_create([Line(booking=booking, product_id=pid,
                                           quantity=q, unit=unit, quoted_unit_price=rate)
                                           for pid, q, unit, rate in lines])
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


def _channel_performance(request):
    """Count enquiry-origin channels, and count conversions ONLY for genuine invoices.

    The month is the booking creation month; an invoice linked later still
    belongs to its original enquiry cohort. No website orders are included.
    """
    month = (request.GET.get("report_month") or timezone.localdate().strftime("%Y-%m")).strip()
    if month != "all":
        try:
            selected = date.fromisoformat(month + "-01")
            if selected.strftime("%Y-%m") != month:
                raise ValueError()
        except ValueError:
            month = timezone.localdate().strftime("%Y-%m")
            selected = date.fromisoformat(month + "-01")
    qs = Booking.objects.all()
    if month != "all":
        qs = qs.filter(created_at__year=selected.year, created_at__month=selected.month)
    entries = list(qs.values("channel", "status", "invoice_reference"))
    refs = {r["invoice_reference"] for r in entries
            if r["status"] == "completed" and r["invoice_reference"]}
    # Validate invoice references against live, non-reversed POS records;
    # manually typed references never count as a sale unless they exist.
    valid_sales = {sale.sale_number: sale for sale in EggPOSSale.objects.filter(
        sale_number__in=refs, is_reversed=False).only("sale_number", "net_total")}
    rows = {channel: {"key": channel, "label": label, "leads": 0,
                      "reconfirmed": 0, "invoiced": 0, "cancelled": 0,
                      "invoiced_value": 0, "conversion": 0.0}
            for channel, label in CHANNELS}
    seen_invoices = set()
    for item in entries:
        row = rows.get(item["channel"])
        if not row:
            continue
        row["leads"] += 1
        if item["status"] in ("confirmed", "completed"):
            row["reconfirmed"] += 1
        if item["status"] == "cancelled":
            row["cancelled"] += 1
        ref = item["invoice_reference"]
        if item["status"] == "completed" and ref in valid_sales and ref not in seen_invoices:
            row["invoiced"] += 1
            row["invoiced_value"] += valid_sales[ref].net_total
            seen_invoices.add(ref)
    for row in rows.values():
        row["conversion"] = round(100.0 * row["invoiced"] / row["leads"], 1) if row["leads"] else 0.0
    total = {"leads": sum(r["leads"] for r in rows.values()),
             "reconfirmed": sum(r["reconfirmed"] for r in rows.values()),
             "invoiced": sum(r["invoiced"] for r in rows.values()),
             "cancelled": sum(r["cancelled"] for r in rows.values()),
             "invoiced_value": sum((r["invoiced_value"] for r in rows.values()), 0)}
    total["conversion"] = round(100.0 * total["invoiced"] / total["leads"], 1) if total["leads"] else 0.0
    return month, list(rows.values()), total


def _context(request, edit_id=None):
    status = (request.GET.get("status") or "active").strip()
    search = (request.GET.get("q") or "").strip()[:80]
    qs = Booking.objects.prefetch_related("lines__product", "events__actor").select_related("created_by")
    today = timezone.localdate()
    if status == "active":
        qs = qs.exclude(status__in=["completed", "cancelled"])
    elif status == "due_today":
        qs = qs.exclude(status__in=["completed", "cancelled"]).filter(expected_date=today)
    elif status == "overdue":
        qs = qs.exclude(status__in=["completed", "cancelled"]).filter(expected_date__lt=today)
    elif status in dict(STATUSES):
        qs = qs.filter(status=status)
    elif status != "all":
        status = "active"
        qs = qs.exclude(status__in=["completed", "cancelled"])
    if search:
        matches = (Q(customer_name__icontains=search) |
                   Q(customer_phone__icontains=search) |
                   Q(delivery_area__icontains=search))
        # RNPRE-000007 is a displayed booking number, generated from the primary key.
        normalized = search.upper().replace(" ", "")
        if normalized.startswith("RNPRE-") and normalized[6:].isdigit():
            matches |= Q(pk=int(normalized[6:]))
        qs = qs.filter(matches)
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
                      quantity=entry.get(f"quantity_{i}") or "", unit=entry.get(f"unit_{i}") or "tray",
                      quoted_unit_price=entry.get(f"quoted_unit_price_{i}") or "") for i in range(1,6)]
        form = dict(customer_name=entry.get('customer_name') or '',customer_phone=entry.get('customer_phone') or '',
                    channel=entry.get('channel') or '', delivery_area=entry.get('delivery_area') or '',
                    expected_date=entry.get('expected_date') or '',
                    delivery_time_from=entry.get('delivery_time_from') or '',
                    delivery_time_to=entry.get('delivery_time_to') or '',
                    notes=entry.get('notes') or '')
        token = entry.get('request_token') or str(uuid.uuid4())
    elif edit_booking:
        form = dict(customer_name=edit_booking.customer_name, customer_phone=edit_booking.customer_phone,
                    channel=edit_booking.channel, delivery_area=edit_booking.delivery_area,
                    expected_date=edit_booking.expected_date.isoformat() if edit_booking.expected_date else '',
                    delivery_time_from=edit_booking.delivery_time_from.strftime('%H:%M') if edit_booking.delivery_time_from else '',
                    delivery_time_to=edit_booking.delivery_time_to.strftime('%H:%M') if edit_booking.delivery_time_to else '',
                    notes=edit_booking.notes)
        lines = [dict(product_id=l.product_id, quantity=l.quantity, unit=l.unit,
                      quoted_unit_price=l.quoted_unit_price if l.quoted_unit_price is not None else '')
                 for l in edit_booking.lines.all()]
        lines += [dict(product_id=0, quantity='', unit='tray', quoted_unit_price='')] * max(0, 5-len(lines))
        token = str(uuid.uuid4())
    else:
        form = dict(customer_name='', customer_phone='', channel='', delivery_area='', expected_date='',
                    delivery_time_from='', delivery_time_to='', notes='')
        lines = [dict(product_id=0, quantity='', unit='tray', quoted_unit_price='') for _ in range(5)]
        token = str(uuid.uuid4())
    selected_customer = None
    if edit_booking and edit_booking.customer_id:
        selected_customer = edit_booking.customer
    elif request.method == 'POST' and request.POST.get('customer_id'):
        selected_customer = EggPOSCustomer.objects.filter(pk=request.POST.get('customer_id'), is_active=True).first() if request.POST.get('customer_id', '').isdigit() else None
    elif request.method == 'GET' and not edit_booking and request.GET.get('customer', '').isdigit():
        selected_customer = EggPOSCustomer.objects.filter(pk=request.GET['customer'], is_active=True).first()
        if selected_customer:
            form['customer_name'] = selected_customer.name
            form['customer_phone'] = selected_customer.phone
            form['delivery_area'] = (selected_customer.address or '')[:180]
    report_month, channel_rows, channel_total = _channel_performance(request)
    counts = {s: Booking.objects.filter(status=s).count() for s, _ in STATUSES}
    active_qs = Booking.objects.exclude(status__in=["completed", "cancelled"])
    counts.update(
        active=active_qs.count(),
        due_today=active_qs.filter(expected_date=today).count(),
        overdue=active_qs.filter(expected_date__lt=today).count(),
    )
    # Dates and badges are computed from real booking due dates, not assumed stock availability.
    for booking in bookings:
        booking.whatsapp_confirmation = build_prebooking_whatsapp(booking)
        booking.is_due_today = bool(booking.expected_date == today and
                                    booking.status not in ("completed", "cancelled"))
        booking.is_overdue = bool(booking.expected_date and booking.expected_date < today and
                                  booking.status not in ("completed", "cancelled"))
    return dict(bookings=bookings, products=products, channels=CHANNELS, units=UNITS,
                statuses=STATUSES, active_status=status, search=search, today=today,
                form=form, line_rows=lines, request_token=token, edit_booking=edit_booking,
                selected_customer=selected_customer,
                report_month=report_month, channel_rows=channel_rows, channel_total=channel_total,
                counts=counts)


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
