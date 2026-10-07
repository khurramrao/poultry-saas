from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum

from api.models.egg_pos import EggPOSInventoryLot, EggPOSSaleAllocation


class Command(BaseCommand):
    help = "Rebuild Egg POS FIFO remaining quantities from active (non-reversed) sale allocations."

    @transaction.atomic
    def handle(self, *args, **options):
        changed = 0
        total_before = 0
        total_after = 0

        lots = EggPOSInventoryLot.objects.select_for_update().all().order_by("id")
        for lot in lots:
            before = int(lot.quantity_remaining or 0)
            sold = int(
                EggPOSSaleAllocation.objects.filter(
                    lot=lot,
                    sale_item__sale__is_reversed=False,
                ).aggregate(total=Sum("quantity"))["total"]
                or 0
            )
            expected = max(int(lot.quantity_received or 0) - sold, 0)
            total_before += before
            total_after += expected

            if before != expected:
                self.stdout.write(
                    f"{lot.lot_code}: {before} -> {expected} eggs "
                    f"(received {lot.quantity_received}, active sold {sold})"
                )
                lot.quantity_remaining = expected
                lot.save(update_fields=["quantity_remaining"])
                changed += 1

        self.stdout.write(self.style.SUCCESS(
            f"Done. Corrected {changed} FIFO lot(s). Total Egg POS stock: "
            f"{total_before} -> {total_after} eggs."
        ))
