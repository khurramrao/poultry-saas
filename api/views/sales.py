from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from api.models.investors import InvestorAllocation, FeedEntry, MedicineEntry

from api.models.sensor import (
    Batch,
    MortalityRecord,
)

from api.models.sales import (
    SaleRecord,
    Expense,
)
from api.services.poultry_inventory import get_batch_bird_position


def attach_current_birds(batches):
    for batch in batches:
        position = get_batch_bird_position(batch)
        batch.current_birds = position["current_birds"]
        batch.total_sold = position["total_sold"]
    return batches


@login_required
def meat_sales_summary(request):
    meat_batches = Batch.objects.filter(
        shed__shed_type="meat"
    ).order_by("-start_date", "batch_number")

    rows = []

    for batch in meat_batches:
        sales = SaleRecord.objects.filter(batch=batch)

        position = get_batch_bird_position(batch)
        total_sold = position["total_sold"]
        total_revenue = sum((sale.total_amount for sale in sales), 0)

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

    position = get_batch_bird_position(batch)
    total_sold = position["total_sold"]
    total_revenue = sum((record.total_amount for record in records), 0)

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

    position = get_batch_bird_position(batch)
    total_birds_sold = position["total_sold"]
    total_revenue = sum((record.total_amount for record in records), 0)

    return render(request, "api/sale_records.html", {
        "batch": batch,
        "records": records,
        "total_birds_sold": total_birds_sold,
        "total_revenue": total_revenue,
    })


@login_required
def close_batch(request, batch_id):
    if not (request.user.is_superuser or request.user.is_staff):
        messages.error(request, "Only Admin can close a batch.")
        return redirect("dashboard")

    batch = get_object_or_404(Batch, id=batch_id)
    position = get_batch_bird_position(batch)
    if position["current_birds"] > 0:
        messages.error(
            request,
            f"Cannot close Batch #{batch.batch_number}: {position['current_birds']:,} system-recorded birds remain. Record counted sales or, after physical stock reaches zero, use ALL BIRDS SOLD in Finance Tracker.",
        )
        return redirect("finance_tracker")

    batch.is_active = False
    batch.end_date = date.today()
    batch.status = "closed"
    batch.save(update_fields=["is_active", "end_date", "status"])
    messages.success(request, f"Batch #{batch.batch_number} closed successfully.")
    return redirect("finance_tracker")


@login_required
def expense_list(request):
    is_admin = request.user.is_superuser or request.user.is_staff
    is_investor = hasattr(request.user, "investor_profile")

    if is_admin:
        batches = Batch.objects.filter(
            is_active=True,
            status="active"
        ).order_by("-start_date", "batch_number")

    elif is_investor:
        investor_batch_ids = InvestorAllocation.objects.filter(
            investor=request.user.investor_profile
        ).values_list("batch_id", flat=True)

        batches = Batch.objects.filter(
            id__in=investor_batch_ids,
            is_active=True,
            status="active"
        ).order_by("-start_date", "batch_number")

    else:
        messages.error(request, "You are not allowed to view expenses.")
        return redirect("dashboard")

    batches = attach_current_birds(batches)
    expense_groups = []

    for batch in batches:
        share_ratio = 1

        if is_investor and not is_admin:
            allocation = InvestorAllocation.objects.filter(
                batch=batch,
                investor=request.user.investor_profile
            ).first()

            share_ratio = (
                allocation.birds_owned / batch.bird_count_initial
                if allocation and batch.bird_count_initial > 0
                else 0
            )

        expenses = Expense.objects.filter(
            batch=batch
        ).order_by("-expense_date", "-id")

        total_expense = 0

        for expense in expenses:
            # Keeps original model fields such as category, notes, etc. working
            expense.display_amount = expense.amount

            if not is_admin:
                expense.display_amount = round(
                    float(expense.amount) * share_ratio,
                    2
                )

            total_expense += float(expense.display_amount)

        expense_groups.append({
            "batch": batch,
            "expenses": expenses,
            "total_expense": total_expense,
        })

    return render(request, "api/expense_list.html", {
        "expense_groups": expense_groups,
        "is_admin": is_admin,
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