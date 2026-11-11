"""Short WhatsApp click-to-chat receipts for RayNoor manual pre-bookings.

Only prepares an outgoing draft. It never sends, invoices or reserves stock.
Supports pre-0091 deployments without quotation/delivery-time fields.
"""
from decimal import Decimal, InvalidOperation
from urllib.parse import quote


def _single_line(value):
    """Prevent untrusted input from injecting extra WhatsApp message lines."""
    return " ".join(str(value or "").split())


def normalize_whatsapp_number(value):
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


def _money(value):
    try:
        return f"Rs {Decimal(str(value)):,.2f}"
    except (InvalidOperation, TypeError, ValueError):
        return None


def _line_label(line):
    qty = int(line.quantity)
    units = {
        "egg": ("egg", "eggs"), "pack6": ("6-egg pack", "6-egg packs"),
        "dozen": ("dozen", "dozen"), "tray": ("tray", "trays"),
    }
    singular, plural = units.get(line.unit, (line.unit, line.unit))
    product_name = _single_line(getattr(line.product, "name", "Eggs"))
    text = f"- {product_name}: {qty} {singular if qty == 1 else plural} ({line.eggs_count} eggs)"
    amount = getattr(line, "quoted_unit_price", None)
    if amount is not None:
        rate = _money(amount)
        if rate is not None:
            text += f" | {rate} per {singular}"
    return text


def _status_content(status):
    if status == "awaiting_stock":
        return ("Your Pre-Booking Confirmation", "Awaiting stock",
                "This confirms receipt only; your order is not yet confirmed. "
                "We'll update you after checking stock and delivery.", "Send Booking Receipt")
    if status == "contact_customer":
        return ("Pre-Booking Stock Update", "Stock reported available - awaiting your reply",
                "Please reply to confirm that you still want this booking.", "Request Confirmation")
    if status == "confirmed":
        return ("Your Pre-Booking Is Confirmed", "Customer reconfirmed",
                "Thank you for confirming. We'll coordinate delivery with you.", "Send Confirmation")
    return ("Thank You for Your Pre-Booking", "Processed",
            "Thank you for your order.", "Send Follow-Up")


def build_prebooking_whatsapp(booking):
    if booking.status == "cancelled":
        return None
    number = normalize_whatsapp_number(booking.customer_phone)
    if not number:
        return None
    lines = list(booking.lines.all())
    if not lines:
        return None
    heading, status, explanation, label = _status_content(booking.status)
    prices = [getattr(line, "quoted_unit_price", None) for line in lines]
    partially_priced = any(price is not None for price in prices) and any(price is None for price in prices)

    content = [
        "*RayNoor Organic Farms*",
        f"*{heading}*",
        "",
        f"Assalam-o-Alaikum {_single_line(booking.customer_name)},",
        "Thank you for choosing *RayNoor Organic Farms*!",
        f"*Ref:* {_single_line(booking.booking_number)}",
        "*Items:*",
        *[_line_label(line) + (" | Rate not set" if partially_priced and price is None else "")
          for line, price in zip(lines, prices)],
        f"*Total:* {sum(line.eggs_count for line in lines)} eggs",
    ]

    # Only display a quote if saved in structured price fields, never inferred
    # from free-form notes or a product's current POS selling price.
    if any(p is not None for p in prices):
        if all(p is not None for p in prices):
            # Fallback is needed for pre-0091-shaped in-memory objects.
            total = getattr(booking, "quoted_items_total", None)
            if total is None:
                total = sum(Decimal(str(price)) * int(line.quantity)
                            for line, price in zip(lines, prices))
            money_total = _money(total)
            if money_total:
                content.append(f"*Quoted Items Total:* {money_total} (delivery charges extra, if any)")
        else:
            content.append("*Quoted Items Total:* Not finalized (some rates missing)")

    if getattr(booking, "expected_date", None):
        content.append(f"*Requested Date:* {booking.expected_date.strftime('%d %b %Y')} (tentative)")
    window = getattr(booking, "delivery_window_display", "") or ""
    if window:
        content.append(f"*Preferred Delivery Time:* {_single_line(window)} (tentative)")
    if getattr(booking, "delivery_area", ""):
        content.append(f"*Address:* {_single_line(booking.delivery_area)}")
    content.extend([
        f"*Status:* {status}",
        explanation,
        "For changes, please reply to this WhatsApp message.",
        "We look forward to serving you!",
    ])
    message = "\n".join(content)
    return {
        "url": f"https://wa.me/{number}?text={quote(message, safe='')}",
        "label": label,
        "message": message,
        "number": number,
    }
