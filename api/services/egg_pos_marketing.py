"""Marketing-only customer attribution for new manual POS sales.

Never books stock, issues invoices, applies payments, or posts accounting entries.
Record attribution inside the existing POS sale transaction.
"""
from collections import defaultdict
from decimal import Decimal
from django.db.models import Q
from api.models.egg_pos_marketing import (
    EggPOSMarketingCustomer as CustomerOrigin,
    EggPOSMarketingSale as SaleOrigin,
    MARKETING_CHANNELS,
)
from api.models.egg_pos_prebooking import EggPOSManualPrebooking as Booking

LABELS = dict(MARKETING_CHANNELS)


def normalize_phone(value):
    raw = "".join(c for c in str(value or "") if c.isdigit())
    if raw.startswith("0092"):
        raw = raw[2:]
    if len(raw) == 11 and raw.startswith("03"):
        raw = "92" + raw[1:]
    elif len(raw) == 10 and raw.startswith("3"):
        raw = "92" + raw
    # Do not guess unknown/local incomplete numbers.
    return raw if 10 <= len(raw) <= 15 else ""


def normalize_name(value):
    return " ".join(str(value or "").strip().casefold().split())[:150]


def first_booking_source(customer_name, normalized_phone):
    """Only exact normalized name+phone; never silently combine family members."""
    name_key = normalize_name(customer_name)
    if not (name_key and normalized_phone):
        return None
    # Narrow by name before normalizing phone formatting in Python.
    candidates = Booking.objects.filter(customer_name__iexact=str(customer_name or "").strip()).order_by(
        "created_at", "pk"
    ).values_list("customer_phone", "channel", "created_at")
    for phone, channel, when in candidates:
        if normalize_phone(phone) == normalized_phone and channel in LABELS:
            return channel, when
    return None


def get_existing_profile(customer, name_key, phone_key):
    if customer is not None:
        # Directory customer ID is stronger than typed name, but not if the
        # currently supplied phone contradicts all matching profiles.
        ids = list(CustomerOrigin.objects.filter(pos_customer=customer).order_by("id"))
        matches = [p for p in ids if p.phone_key == phone_key]
        if len(matches) == 1:
            return matches[0]
        if ids:  # conflicting phone or more than one profile for this directory customer
            return None
    match = CustomerOrigin.objects.filter(name_key=name_key, phone_key=phone_key).first()
    if match and customer is not None and match.pos_customer_id not in (None, customer.pk):
        return None  # identical labels must not merge different POS customer records
    return match


def record_marketing_sale(sale, booking, order_source, user):
    """Call once for a completed manual POS sale *inside* its transaction.

    Unknown phone/name => order source is still saved, no customer profile.
    """
    source = booking.channel if booking is not None else order_source
    if source not in LABELS or (booking is None and source == "website"):
        raise ValueError("Choose a valid order source before completing the POS sale.")
    name = (sale.customer_name or "").strip()
    phone_key = normalize_phone(sale.customer_phone)
    name_key = normalize_name(name)
    profile = None
    if name_key in {"walk-in", "walk in", "customer", "anonymous", "unknown"}:
        name_key = ""  # generic receipt labels cannot identify returning customers
    if name_key and phone_key:
        customer = sale.customer
        profile = get_existing_profile(customer, name_key, phone_key)
        if profile is None:
            # Shared phone with a different name stays a separate profile.
            # If a directory record has another conflicting profile do not guess.
            conflicting = customer is not None and (
                CustomerOrigin.objects.filter(pos_customer=customer).exists()
                or CustomerOrigin.objects.filter(name_key=name_key, phone_key=phone_key)
                   .exclude(Q(pos_customer__isnull=True) | Q(pos_customer=customer)).exists()
            )
            if not conflicting:
                first = first_booking_source(name, phone_key)
                initial_source = first[0] if first else source
                kwargs = {"name": name, "name_key": name_key, "phone_key": phone_key}
                profile, created = CustomerOrigin.objects.get_or_create(
                    name_key=name_key, phone_key=phone_key,
                    defaults={**kwargs, "pos_customer": customer,
                              "acquired_from": initial_source, "created_by": user,
                              **({"acquired_at": first[1]} if first else {})},
                )
                if not created and profile.pos_customer_id is None and customer is not None:
                    profile.pos_customer = customer
                    profile.save(update_fields=["pos_customer"])
    SaleOrigin.objects.create(sale=sale, marketing_customer=profile,
                              order_source=source, recorded_by=user)


def decorate_sales(sales):
    """Annotate list objects without modifying any historical database row."""
    listed = list(sales)
    sale_ids = [s.pk for s in listed]
    entries = {x.sale_id: x for x in SaleOrigin.objects.filter(
        sale_id__in=sale_ids
    ).select_related("marketing_customer")}
    profile_ids = {x.marketing_customer_id for x in entries.values() if x.marketing_customer_id}
    first_sale = {}
    if profile_ids:
        for profile_id, sale_id in SaleOrigin.objects.filter(
            marketing_customer_id__in=profile_ids, sale__is_reversed=False,
        ).order_by("sale__sale_date", "sale_id").values_list("marketing_customer_id", "sale_id"):
            first_sale.setdefault(profile_id, sale_id)
    for sale in listed:
        a = entries.get(sale.pk)
        sale.raynoor_acquired_label = "Untracked"
        sale.raynoor_repeat_customer = False
        if a:
            if not getattr(sale, "online_order_id", None):
                sale.raynoor_origin_key = a.order_source
                sale.raynoor_origin_label = LABELS[a.order_source]
            if a.marketing_customer_id:
                sale.raynoor_acquired_label = LABELS[a.marketing_customer.acquired_from]
                sale.raynoor_repeat_customer = first_sale.get(a.marketing_customer_id) != sale.pk
        # Older POS invoices without attribution are intentionally not
        # merged or backfilled by phone alone.
    return listed


def acquisition_report():
    """All-time cohort performance for *tracked* customers and non-reversed sales.

    Totals intentionally exclude historical invoices without a verified profile.
    """
    profiles = list(CustomerOrigin.objects.values_list("pk", "acquired_from"))
    source_by_profile = {pk: source for pk, source in profiles}
    report = {code: {"label": label, "customers": 0, "orders": 0,
                     "repeat_orders": 0, "revenue": Decimal("0.00")}
              for code, label in MARKETING_CHANNELS}
    for _, channel in profiles:
        report[channel]["customers"] += 1
    counts = defaultdict(int)
    for profile_id, amount in SaleOrigin.objects.filter(
        marketing_customer__isnull=False,
        sale__is_reversed=False,
    ).order_by("sale__sale_date", "sale_id").values_list("marketing_customer_id", "sale__net_total"):
        channel = source_by_profile.get(profile_id)
        if channel not in report:
            continue
        row = report[channel]
        row["orders"] += 1
        if counts[profile_id]:
            row["repeat_orders"] += 1
        counts[profile_id] += 1
        row["revenue"] += amount or Decimal("0.00")
    return [report[key] for key, _ in MARKETING_CHANNELS]
