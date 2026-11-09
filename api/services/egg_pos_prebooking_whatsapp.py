"""WhatsApp click-to-chat links for RayNoor manual pre-bookings.

This prepares a message; it never sends anything, changes booking status,
reserves stock, or records a customer confirmation.
"""
from urllib.parse import quote


def _single_line(value):
    """Keep customer-entered text from creating misleading extra message lines."""
    return " ".join(str(value or "").split())


def normalize_whatsapp_number(value):
    """Return digits in international format; accept common Pakistani entries.

    Unknown 10-digit numbers are deliberately rejected instead of guessing a
    country code. Other already-international numbers are retained when valid.
    """
    digits = "".join(c for c in str(value or "") if "0" <= c <= "9")
    if digits.startswith("0092"):
        digits = digits[2:]
    if digits.startswith("03") and len(digits) == 11:
        digits = "92" + digits[1:]
    elif len(digits) == 10 and digits.startswith("3"):
        digits = "92" + digits
    if digits.startswith("0") or not (11 <= len(digits) <= 15):
        return ""
    return digits


def _line_label(line):
    qty = int(line.quantity)
    unit = line.unit
    labels = {
        "egg": ("egg", "eggs"),
        "pack6": ("6-egg pack", "6-egg packs"),
        "dozen": ("dozen", "dozen"),
        "tray": ("tray", "trays"),
    }
    singular, plural = labels.get(unit, (unit, unit))
    desc = singular if qty == 1 else plural
    product = _single_line(getattr(line.product, "name", "Eggs"))
    return f"- {product}: {qty} {desc} ({line.eggs_count} eggs)"


def build_prebooking_whatsapp(booking):
    """Return WhatsApp URL, label and text or None for an unusable number.

    Only a human clicking Send in WhatsApp transmits the message. In particular,
    'awaiting_stock' is an acknowledgement rather than a stock confirmation.
    """
    if booking.status == "cancelled":
        return None
    number = normalize_whatsapp_number(booking.customer_phone)
    if not number:
        return None

    if booking.status == "awaiting_stock":
        heading = "Pre-Booking Received"
        status = "Awaiting stock"
        explanation = (
            "Thank you for your pre-booking. We will contact you to confirm "
            "availability and delivery. This message acknowledges your request; "
            "your order is not yet confirmed."
        )
        label = "Send Booking Receipt"
    elif booking.status == "contact_customer":
        heading = "Pre-Booking Stock Update"
        status = "Stock reported available - awaiting your reply"
        explanation = (
            "We are following up on your pre-booking. Please reply to confirm "
            "you still require these items. We will finalize the order after "
            "your confirmation."
        )
        label = "Request Confirmation"
    elif booking.status == "confirmed":
        heading = "Pre-Booking Confirmation"
        status = "Customer reconfirmed"
        explanation = (
            "Thank you for confirming your pre-booking. Our team will "
            "coordinate the next steps with you."
        )
        label = "Send Confirmation"
    else:
        heading = "Pre-Booking Follow-Up"
        status = "Processed"
        explanation = "Thank you for choosing RayNoor Organic Farms."
        label = "Send Follow-Up"

    name = _single_line(booking.customer_name)
    lines = list(booking.lines.all())
    if not lines:
        return None
    content = [
        "*RayNoor Organic Farms*",
        f"*{heading}*",
        "",
        f"Assalam-o-Alaikum {name},",
        "",
        f"*Booking No:* {booking.booking_number}",
        "*Requested Items:*",
        *[_line_label(line) for line in lines],
        f"*Total:* {sum(line.eggs_count for line in lines)} eggs",
    ]
    if booking.expected_date:
        content.append(f"*Requested Date:* {booking.expected_date.strftime('%d %b %Y')} (tentative)")
    if booking.delivery_area:
        content.append(f"*Delivery Location:* {_single_line(booking.delivery_area)}")
    content += [
        f"*Status:* {status}",
        "",
        explanation,
        "",
        "Thank you,",
        "*RayNoor Organic Farms*",
    ]
    message = "\n".join(content)
    return {
        "url": f"https://wa.me/{number}?text={quote(message, safe='')}",
        "label": label,
        "message": message,
        "number": number,
    }
