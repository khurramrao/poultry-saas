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
        heading = "Your Pre-Booking Confirmation"
        opening = "This is your pre-booking confirmation of receipt. We have received your request."
        status = "Awaiting stock"
        explanation = (
            "This confirms receipt of your booking request only. Stock availability "
            "and the requested delivery schedule are not yet confirmed; "
            "your order is not yet confirmed. We will contact you with an update."
        )
        label = "Send Booking Receipt"
    elif booking.status == "contact_customer":
        heading = "Pre-Booking Stock Update"
        opening = "We have an update regarding your pre-booking."
        status = "Stock reported available - awaiting your reply"
        explanation = (
            "Please reply to confirm you still require these items. "
            "We will finalize the order after your confirmation."
        )
        label = "Request Confirmation"
    elif booking.status == "confirmed":
        heading = "Your Pre-Booking Is Confirmed"
        opening = "Your pre-booking has been confirmed."
        status = "Customer reconfirmed"
        explanation = (
            "Thank you for confirming your pre-booking. Our team will "
            "coordinate the next steps with you."
        )
        label = "Send Confirmation"
    else:
        heading = "Thank You for Your Pre-Booking"
        opening = "We appreciate the opportunity to serve you."
        status = "Processed"
        explanation = "Thank you for choosing RayNoor Organic Farms."
        label = "Send Follow-Up"

    name = _single_line(booking.customer_name)
    lines = list(booking.lines.all())
    if not lines:
        return None
    content = [
        "*RayNoor Organic Farms*",
        "_Committed to Organic Excellence_",
        "",
        f"*{heading}*",
        "",
        f"Assalam-o-Alaikum {name},",
        "",
        "Thank you for choosing *RayNoor Organic Farms*!",
        opening,
        "",
        "*BOOKING DETAILS*",
        f"*Booking No:* {_single_line(booking.booking_number)}",
        "*Requested Items:*",
        *[_line_label(line) for line in lines],
        f"*Total:* {sum(line.eggs_count for line in lines)} eggs",
    ]
    # Compatibility with pre-0091 bookings/models: quote and delivery-window
    # fields may not exist on Railway if the optional earlier patch was omitted.
    # Missing rates must NEVER be represented as Rs 0 or guessed from product prices.
    priced_lines = [(line, getattr(line, "quoted_unit_price", None)) for line in lines]
    if any(price is not None for _, price in priced_lines):
        content.append("*Quoted Rates (not an invoice):*")
        for line, price in priced_lines:
            if price is not None:
                line_total = getattr(line, "quoted_line_total", None)
                if line_total is None:
                    line_total = price * line.quantity
                unit_name = getattr(line, "quoted_rate_unit", None) or {
                    "egg": "egg", "pack6": "6-egg pack",
                    "dozen": "dozen", "tray": "tray",
                }.get(line.unit, line.unit)
                content.append(
                    f"- {_single_line(line.product.name)}: Rs {price:,.2f} "
                    f"per {unit_name} = Rs {line_total:,.2f}"
                )
            else:
                content.append(f"- {_single_line(line.product.name)}: Rate not set")
        quoted_total = getattr(booking, "quoted_items_total", None)
        if quoted_total is None and all(price is not None for _, price in priced_lines):
            quoted_total = sum(price * line.quantity for line, price in priced_lines)
        if quoted_total is not None:
            content.append(f"*Quoted Items Total:* Rs {quoted_total:,.2f}")
        else:
            content.append("*Quoted Items Total:* Not finalized (some rates missing)")
        content.append("Delivery charges, if applicable, are not included.")

    # The legacy pre-booking model has expected_date and delivery_area but no
    # delivery_window_display property. Both render safely in either version.
    delivery_window = getattr(booking, "delivery_window_display", "") or ""
    if booking.expected_date or delivery_window or booking.delivery_area:
        content.extend(["", "*DELIVERY PREFERENCES*"])
    if booking.expected_date:
        content.append(f"*Requested Date:* {booking.expected_date.strftime('%d %b %Y')} (tentative)")
    if delivery_window:
        content.append(f"*Preferred Delivery Time:* {delivery_window} (tentative)")
    if booking.delivery_area:
        content.append(f"*Delivery Location:* {_single_line(booking.delivery_area)}")
    content += [
        "",
        "*BOOKING STATUS*",
        f"*Status:* {status}",
        explanation,
        "",
        "For any questions or changes, please reply to this WhatsApp message.",
        "",
        "Thank you for trusting *RayNoor Organic Farms*.",
        "We look forward to serving you!",
        "*RayNoor Organic Farms*",
    ]
    message = "\n".join(content)
    return {
        "url": f"https://wa.me/{number}?text={quote(message, safe='')}",
        "label": label,
        "message": message,
        "number": number,
    }
