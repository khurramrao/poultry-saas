"""Poultry bird-count helpers used by sales, dashboards and finance."""
from django.db.models import Sum

from api.models.sensor import MortalityRecord
from api.models.sales import BatchBirdSaleReconciliation, SaleRecord


def get_active_reconciliation(batch):
    return (
        BatchBirdSaleReconciliation.objects
        .filter(batch=batch, is_active=True)
        .select_related("confirmed_by", "reversed_by")
        .first()
    )


def get_batch_bird_position(batch):
    """Return the recorded/effective bird position for one batch.

    Counted sales reduce the running system bird count immediately.
    Weight-only sales do not reduce bird count because the quantity is unknown.
    Once Admin confirms "All Birds Sold", the reconciliation supplies the
    otherwise-unknown sold quantity and sets physical/current stock to zero.
    """
    mortality = int(
        MortalityRecord.objects.filter(batch=batch)
        .aggregate(total=Sum("count"))["total"]
        or 0
    )
    counted_sold = int(
        SaleRecord.objects.filter(batch=batch)
        .aggregate(total=Sum("birds_sold"))["total"]
        or 0
    )
    reconciliation = get_active_reconciliation(batch)
    reconciled_weight_only = (
        int(reconciliation.reconciled_weight_only_birds or 0)
        if reconciliation
        else 0
    )
    total_sold = counted_sold + reconciled_weight_only

    if reconciliation:
        current_birds = 0
    else:
        current_birds = max(
            int(batch.bird_count_initial or 0) - mortality - counted_sold,
            0,
        )

    return {
        "mortality": mortality,
        "counted_sold": counted_sold,
        "reconciled_weight_only_birds": reconciled_weight_only,
        "total_sold": total_sold,
        "current_birds": current_birds,
        "reconciliation": reconciliation,
        "all_birds_sold": bool(reconciliation),
    }
