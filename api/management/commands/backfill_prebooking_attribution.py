"""Opt-in, safely limited backfill of *verified* old manual pre-booking invoices.

Default is dry-run. Never infers repeat customers from phone alone.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from api.models.egg_pos import EggPOSSale
from api.models.egg_pos_prebooking import EggPOSManualPrebooking
from api.models.egg_pos_marketing import EggPOSMarketingSale
from api.services.egg_pos_marketing import record_marketing_sale, normalize_name, normalize_phone


class Command(BaseCommand):
    help = "Backfill only verified completed manual prebook invoices; safe dry-run by default."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Write verified missing attribution rows")

    def handle(self, *args, **options):
        bookings = EggPOSManualPrebooking.objects.filter(
            status="completed"
        ).exclude(invoice_reference="").order_by("created_at", "id")
        counts = {"eligible": 0, "skipped": 0, "created": 0}
        for b in bookings:
            sales = list(EggPOSSale.objects.filter(sale_number=b.invoice_reference, is_reversed=False))
            if len(sales) != 1:
                counts["skipped"] += 1
                continue
            if EggPOSMarketingSale.objects.filter(sale=sales[0]).exists():
                counts["skipped"] += 1
                continue
            sale = sales[0]
            # A verified link alone is not enough if booking and sale identities
            # conflict. User can review such records manually.
            if (normalize_name(b.customer_name) != normalize_name(sale.customer_name)
                    or not normalize_phone(b.customer_phone)
                    or normalize_phone(b.customer_phone) != normalize_phone(sale.customer_phone)):
                counts["skipped"] += 1
                continue
            if EggPOSManualPrebooking.objects.filter(status="completed", invoice_reference=b.invoice_reference).count() != 1:
                counts["skipped"] += 1
                continue
            counts["eligible"] += 1
            if options["apply"]:
                with transaction.atomic():
                    sale = EggPOSSale.objects.select_for_update().get(pk=sale.pk)
                    if not EggPOSMarketingSale.objects.filter(sale=sale).exists():
                        record_marketing_sale(sale, b, b.channel, None)
                        counts["created"] += 1
        self.stdout.write(str(counts) + (" (applied)" if options["apply"] else " (dry-run; add --apply to write)"))
