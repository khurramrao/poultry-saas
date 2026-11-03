"""Read-only Egg POS marketing reports. Never creates invoices or edits financial data.

Order-source reporting includes ALL non-reversed POS invoices (including unpaid).
Acquisition/repeat reporting includes only verified marketing-customer profiles.
"""
from collections import defaultdict
from decimal import Decimal

from api.models.egg_pos import EggPOSSale
from api.models.egg_pos_marketing import (
    EggPOSMarketingCustomer, EggPOSMarketingSale, MARKETING_CHANNELS,
)
from api.models.egg_pos_prebooking import EggPOSManualPrebooking
from api.models.online_shop import OnlineOrder

ZERO = Decimal("0.00")
CHANNEL_KEYS = tuple(key for key, label in MARKETING_CHANNELS)
CHANNEL_NAMES = dict(MARKETING_CHANNELS)
CHANNEL_NAMES["direct"] = "Direct / Unattributed"
CHANNEL_NAMES["ambiguous"] = "Ambiguous / Unattributed"


def build_source_report(sales_rows, online_sale_ids, marketing_sale_sources, booking_sources):
    """Pure aggregation: never infer a social channel from customer name or phone.

    sales_rows: iterable of (sale_id, sale_number, net_total) for non-reversed sales
    online_sale_ids: set of invoice IDs linked to real online orders
    marketing_sale_sources: dict sale_id -> explicit recorded order source
    booking_sources: dict invoice number -> iterable of completed booking channels

    Where sources disagree, the actual website link takes precedence, followed
    by a unique completed pre-booking link. Multiple incompatible pre-booking
    sources are reported as ambiguous, not arbitrarily assigned.
    """
    rows = {key: {"key": key, "label": CHANNEL_NAMES[key],
                  "invoices": 0, "revenue": ZERO} for key in CHANNEL_NAMES}
    total = {"invoices": 0, "revenue": ZERO}
    online_sale_ids = set(online_sale_ids)
    for sale_id, invoice_ref, amount in sales_rows:
        booking_matches = {channel for channel in booking_sources.get(invoice_ref, ())
                           if channel in CHANNEL_KEYS}
        if sale_id in online_sale_ids:
            channel = "website"
        elif len(booking_matches) > 1:
            channel = "ambiguous"
        elif len(booking_matches) == 1:
            channel = next(iter(booking_matches))
        else:
            channel = marketing_sale_sources.get(sale_id, "direct")
            if channel not in CHANNEL_KEYS:
                channel = "ambiguous"
            # A 'website' source without a verified RNWEB link must not imply
            # that an online-store invoice exists.
            if channel == "website":
                channel = "ambiguous"
        value = Decimal(amount or 0)
        rows[channel]["invoices"] += 1
        rows[channel]["revenue"] += value
        total["invoices"] += 1
        total["revenue"] += value
    ordered = [rows[key] for key in CHANNEL_KEYS] + [rows["ambiguous"]]
    return ordered, total


def order_source_report():
    """All-time invoices, not limited by the Sales screen's 200-row display."""
    # Use streaming iteration to avoid loading entire Django model objects.
    # The website link is actual OnlineOrder.pos_sale, not an inferred platform.
    website_ids = OnlineOrder.objects.filter(pos_sale__isnull=False).values_list(
        "pos_sale_id", flat=True
    )
    explicit_sources = dict(EggPOSMarketingSale.objects.values_list(
        "sale_id", "order_source"
    ))
    booking_sources = defaultdict(list)
    for invoice, channel in EggPOSManualPrebooking.objects.filter(
        status="completed"
    ).exclude(invoice_reference="").values_list("invoice_reference", "channel"):
        booking_sources[invoice].append(channel)
    invoices = EggPOSSale.objects.filter(is_reversed=False).values_list(
        "id", "sale_number", "net_total"
    ).iterator(chunk_size=2000)
    return build_source_report(invoices, website_ids, explicit_sources, booking_sources)


def build_repeat_report(profiles, sales_rows):
    """Repeat invoices/revenue belong to the customer's ORIGINAL platform.

    sales_rows must be ordered by sale date + ID and contain only non-reversed
    invoices linked to a confirmed marketing profile.
    """
    source_by_profile = dict(profiles)
    out = {code: {"label": label, "repeat_customers": 0,
                  "repeat_orders": 0, "repeat_revenue": ZERO}
           for code, label in MARKETING_CHANNELS}
    seen = defaultdict(int)
    for profile_id, value in sales_rows:
        channel = source_by_profile.get(profile_id)
        if channel not in out:
            continue
        if seen[profile_id] == 1:
            out[channel]["repeat_customers"] += 1
        if seen[profile_id]:
            out[channel]["repeat_orders"] += 1
            out[channel]["repeat_revenue"] += Decimal(value or 0)
        seen[profile_id] += 1
    return [out[code] for code, _ in MARKETING_CHANNELS]


def repeat_customer_report():
    profiles = EggPOSMarketingCustomer.objects.values_list("id", "acquired_from")
    sales = EggPOSMarketingSale.objects.filter(
        marketing_customer__isnull=False, sale__is_reversed=False
    ).order_by("sale__sale_date", "sale_id").values_list(
        "marketing_customer_id", "sale__net_total"
    )
    return build_repeat_report(profiles, sales)
