"""Bridge from a *confirmed* manual social booking to the EXISTING Egg POS sale.

No stock allocation, payment or invoice is performed here. Only Egg POS new_sale
may create invoices and it calls complete_with_sale inside its own transaction.
"""
from collections import OrderedDict

from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404
from django.utils import timezone

from api.models.egg_pos_prebooking import (
    EggPOSManualPrebooking as Booking,
    EggPOSManualPrebookingEvent as Event,
    UNIT_MULTIPLIER,
)
from api.services.egg_pos import can_manage


def ensure_prebooking_admin(user):
    if not (user.is_authenticated and (user.is_superuser or (user.is_staff and can_manage(user)))):
        raise PermissionDenied("Only Egg POS administrators can convert manual pre-bookings.")


def digits(phone):
    s = "".join(c for c in str(phone or "") if c.isdigit())
    if s.startswith("0092"):
        s = s[2:]
    if s.startswith("92") and len(s) == 12:
        s = "0" + s[2:]
    return s


def prefill_for_sale(user, raw_id, customers):
    """Safe GET-only prefill, never creates a customer, stock reservation or payment."""
    ensure_prebooking_admin(user)
    booking = get_object_or_404(
        Booking.objects.prefetch_related("lines__product"), pk=raw_id, status="confirmed"
    )
    grouped = OrderedDict()
    for line in booking.lines.all():
        key = line.product_id
        eggs = int(line.quantity) * UNIT_MULTIPLIER.get(line.unit, 0)
        if key not in grouped:
            grouped[key] = {"product_id": key, "eggs": 0, "unit": line.unit,
                            "unit_count": line.quantity}
        else:
            # POS supports only one cart row per product. Combine repeated
            # product rows as loose eggs, never silently drop an item.
            grouped[key]["unit"] = "egg"
        grouped[key]["eggs"] += eggs
    for item in grouped.values():
        if item["unit"] == "pack6" or item["unit"] == "egg":
            item["unit"] = "egg"
            item["unit_count"] = item["eggs"]

    matching_customer_id = None
    target = digits(booking.customer_phone)
    if target:
        for customer in customers:
            if digits(customer.phone) == target:
                matching_customer_id = customer.id
                break

    return {
        "id": booking.id,
        "booking_number": booking.booking_number,
        "channel": booking.get_channel_display(),
        "customer_name": booking.customer_name,
        "customer_phone": booking.customer_phone,
        "delivery_area": booking.delivery_area,
        "notes": booking.notes,
        "items": list(grouped.values()),
        "customer_id": matching_customer_id,
    }


def lock_confirmed_for_sale(user, raw_id):
    """Call inside transaction.atomic BEFORE locking stock/creating a sale."""
    ensure_prebooking_admin(user)
    try:
        booking_id = int(raw_id)
    except (TypeError, ValueError):
        raise ValidationError("Invalid pre-booking reference.")
    booking = get_object_or_404(Booking.objects.select_for_update(), pk=booking_id)
    if booking.status != "confirmed" or booking.invoice_reference:
        raise ValidationError(
            "This pre-booking is no longer awaiting an invoice. Reload the register before trying again."
        )
    return booking


def complete_with_sale(booking, sale, user):
    """Only called after normal POS sale and payment posting, in SAME transaction."""
    if booking.status != "confirmed" or booking.invoice_reference or not sale.sale_number:
        raise ValidationError("Pre-booking conversion changed; POS invoice was not completed.")
    booking.status = "completed"
    booking.completed_at = timezone.now()
    booking.invoice_reference = sale.sale_number
    booking.save(update_fields=["status", "completed_at", "invoice_reference", "updated_at"])
    Event.objects.create(
        booking=booking, actor=user, action="completed",
        details=f"POS invoice {sale.sale_number} created and linked by staff; payment posted ONLY as entered in POS."
    )
