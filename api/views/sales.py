import json
from datetime import date, timedelta
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt

from api.models.sensor import (
    Batch,
    Device,
    MortalityRecord,
    SensorData,
    Shed,
    VaccineRecord,
    VaccineSchedule,

)

from api.models.temperature import TemperatureRule
from api.models.sales import SaleRecord

@login_required
def meat_sales_summary(request):
    meat_batches = Batch.objects.filter(
        shed__shed_type="meat"
    ).order_by("-start_date", "batch_number")

    rows = []

    for batch in meat_batches:
        sales = SaleRecord.objects.filter(batch=batch)

        total_sold = sum(sales.values_list("birds_sold", flat=True))
        total_revenue = sum(sale.total_amount for sale in sales)

        rows.append({
            "batch": batch,
            "total_sold": total_sold,
            "total_revenue": total_revenue,
            "sales_count": sales.count(),
        })

    return render(request, "api/meat_sales_summary.html", {
        "rows": rows
    })
@login_required
def meat_sale_detail(request, batch_id):
    batch = get_object_or_404(Batch, id=batch_id)

    records = SaleRecord.objects.filter(batch=batch).order_by("-sale_date")

    total_sold = sum(records.values_list("birds_sold", flat=True))
    total_revenue = sum(record.total_amount for record in records)

    return render(request, "api/meat_sale_detail.html", {
        "batch": batch,
        "records": records,
        "total_sold": total_sold,
        "total_revenue": total_revenue,
    })
@login_required
def sale_records(request, batch_id):
    batch = get_object_or_404(Batch, id=batch_id)

    records = SaleRecord.objects.filter(batch=batch).order_by('-sale_date')

    total_birds_sold = sum(records.values_list('birds_sold', flat=True))
    total_revenue = sum(record.total_amount for record in records)

    return render(request, "api/sale_records.html", {
        "batch": batch,
        "records": records,
        "total_birds_sold": total_birds_sold,
        "total_revenue": total_revenue,
    })

def close_batch(request, batch_id):
    batch = get_object_or_404(Batch, id=batch_id)

    batch.is_active = False
    batch.end_date = date.today()

    if hasattr(batch, "status"):
        batch.status = "sold"

    batch.save()

    return redirect("dashboard")