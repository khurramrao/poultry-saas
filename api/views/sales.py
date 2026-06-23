from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from api.models.sensor import (
    Batch,
    MortalityRecord,
)

from api.models.sales import (
    SaleRecord,
    Expense,
)


def attach_current_birds(batches):
    for batch in batches:
        total_mortality = MortalityRecord.objects.filter(
            batch=batch
        ).aggregate(total=Sum("count"))["total"] or 0

        total_sold = SaleRecord.objects.filter(
            batch=batch
        ).aggregate(total=Sum("birds_sold"))["total"] or 0

        batch.current_birds = (
            batch.bird_count_initial
            - total_mortality
            - total_sold
        )

    return batches


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
        "rows": rows,
    })


@login_required
def meat_sale_detail(request, batch_id):
    batch = get_object_or_404(Batch, id=batch_id)

    records = SaleRecord.objects.filter(
        batch=batch
    ).order_by("-sale_date")

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

    records = SaleRecord.objects.filter(
        batch=batch
    ).order_by("-sale_date")

    total_birds_sold = sum(records.values_list("birds_sold", flat=True))
    total_revenue = sum(record.total_amount for record in records)

    return render(request, "api/sale_records.html", {
        "batch": batch,
        "records": records,
        "total_birds_sold": total_birds_sold,
        "total_revenue": total_revenue,
    })


@login_required
def close_batch(request, batch_id):
    batch = get_object_or_404(Batch, id=batch_id)

    batch.is_active = False
    batch.end_date = date.today()
    batch.status = "closed"
    batch.save()

    return redirect("dashboard")


@login_required
def expense_list(request):
    is_admin = request.user.is_superuser or request.user.is_staff

    if not is_admin:
        messages.error(request, "Only admin can view expenses.")
        return redirect("dashboard")

    batches = Batch.objects.filter(
        is_active=True,
        status="active"
    ).order_by("-start_date", "batch_number")

    batches = attach_current_birds(batches)

    expense_groups = []

    for batch in batches:
        expenses = Expense.objects.filter(
            batch=batch
        ).order_by("-expense_date", "-id")

        total_expense = sum(exp.amount for exp in expenses)

        expense_groups.append({
            "batch": batch,
            "expenses": expenses,
            "total_expense": total_expense,
        })

    return render(request, "api/expense_list.html", {
        "expense_groups": expense_groups,
    })


@login_required
@require_http_methods(["GET", "POST"])
def add_expense(request):
    is_admin = request.user.is_superuser or request.user.is_staff

    if not is_admin:
        messages.error(request, "Only admin can add expenses.")
        return redirect("dashboard")

    batches = Batch.objects.filter(
        is_active=True,
        status="active"
    ).order_by("-start_date", "batch_number")

    batches = attach_current_birds(batches)

    if request.method == "POST":
        batch_id = request.POST.get("batch_id")
        category = request.POST.get("category")
        amount = request.POST.get("amount")
        expense_date = request.POST.get("expense_date")
        description = request.POST.get("description", "")

        batch = get_object_or_404(Batch, id=batch_id)

        if batch.status == "closed" or not batch.is_active:
            messages.error(request, "This batch is closed.")
            return redirect("expense_list")

        Expense.objects.create(
            batch=batch,
            category=category,
            amount=amount,
            expense_date=expense_date,
            description=description,
        )

        messages.success(request, "Expense added successfully.")
        return redirect("expense_list")

    return render(request, "api/add_expense.html", {
        "batches": batches,
    })