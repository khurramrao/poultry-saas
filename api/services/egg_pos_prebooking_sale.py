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
    UNIT_MULTIPLIER, EGG_UNITS,
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
        # This pre-fill uses physical units (eggs for eggs, jars for ghee/honey).
        # The existing JS cart field named `eggs` is a legacy stock-quantity key.
        if line.product.is_egg:
            if line.unit not in EGG_UNITS:
                raise ValidationError("An egg pre-booking has an invalid unit; edit the booking first.")
            stock_quantity = int(line.quantity) * UNIT_MULTIPLIER[line.unit]
            wanted_unit = line.unit
        else:
            if line.unit != "jar":
                raise ValidationError(
                    f"{line.product.name} was booked with an egg unit. "
                    "Edit the pre-booking and select Sealed Jars before invoicing."
                )
            stock_quantity = int(line.quantity)
            wanted_unit = "jar"
        key = line.product_id
        if key not in grouped:
            grouped[key] = {"product_id": key, "eggs": stock_quantity,
                            "unit": wanted_unit, "unit_count": line.quantity,
                            "stock_unit": "egg" if line.product.is_egg else "jar"}
        else:
            # The POS supports one cart row per product. Combine repeated rows
            # as loose eggs or jars, never as a tray of jars.
            grouped[key]["eggs"] += stock_quantity
            grouped[key]["unit"] = "egg" if line.product.is_egg else "jar"
            grouped[key]["unit_count"] = grouped[key]["eggs"]
    for item in grouped.values():
        if item["unit"] in ("egg", "pack6", "jar"):
            item["unit"] = "egg" if item["stock_unit"] == "egg" else "jar"
            item["unit_count"] = item["eggs"]

    # Prefer the explicit saved customer link; phone matching is fallback only
    # for historical pre-bookings created before the customer-link feature.
    matching_customer_id = booking.customer_id if any(
        customer.id == booking.customer_id for customer in customers
    ) else None
    target = digits(booking.customer_phone)
    if matching_customer_id is None and target:
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
