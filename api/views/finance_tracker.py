from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.contrib import messages
from django.views.decorators.http import require_http_methods
from django.db.models import Sum

from decimal import Decimal
import math

from api.models.sensor import Batch, MortalityRecord
from api.models.sales import ChickCostEntry, SaleRecord, Expense
from api.models.investors import InvestorAllocation, FeedEntry, MedicineEntry


@login_required
def finance_tracker(request):
    is_admin = request.user.is_superuser or request.user.is_staff

    if is_admin:
        batches = Batch.objects.filter(is_active=True).order_by("-start_date", "batch_number")
    else:
        if not hasattr(request.user, "investor_profile"):
            return redirect("dashboard")

        investor_batch_ids = InvestorAllocation.objects.filter(
            investor=request.user.investor_profile
        ).values_list("batch_id", flat=True)

        batches = Batch.objects.filter(
            id__in=investor_batch_ids,
            is_active=False
        ).order_by("-end_date", "-start_date", "batch_number")

    finance_rows = []

    for batch in batches:
        total_mortality = MortalityRecord.objects.filter(batch=batch).aggregate(
            total=Sum("count")
        )["total"] or 0

        sales_records = SaleRecord.objects.filter(batch=batch).order_by("-sale_date", "-id")

        total_sold = sales_records.aggregate(
            total=Sum("birds_sold")
        )["total"] or 0

        total_sales_revenue = sum(float(sale.total_amount) for sale in sales_records)
        total_discount = sum(float(sale.discount_amount) for sale in sales_records)
        total_sale_weight = sum(float(sale.total_weight_kg) for sale in sales_records)
        gross_sales_revenue = sum(float(sale.gross_amount) for sale in sales_records)

        average_sale_weight = round(total_sale_weight / total_sold, 3) if total_sold > 0 else 0
        average_sale_rate = round(gross_sales_revenue / total_sale_weight, 2) if total_sale_weight > 0 else 0

        current_birds = batch.bird_count_initial - total_mortality - total_sold

        expenses = Expense.objects.filter(batch=batch)
        total_expenses = expenses.aggregate(total=Sum("amount"))["total"] or 0

        # NEW COGS LOGIC — BatchCost discontinued
        chick_cost = ChickCostEntry.objects.filter(batch=batch).aggregate(
            total=Sum("chick_cost")
        )["total"] or 0

        carriage_cost = ChickCostEntry.objects.filter(batch=batch).aggregate(
            total=Sum("carriage_cost")
        )["total"] or 0

        feed_cost = FeedEntry.objects.filter(batch=batch).aggregate(
            total=Sum("amount")
        )["total"] or 0

        medicine_cost = MedicineEntry.objects.filter(batch=batch).aggregate(
            total=Sum("amount")
        )["total"] or 0

        total_cogs = chick_cost + carriage_cost + feed_cost + medicine_cost

        current_chick_cost_per_bird = 0
        if current_birds > 0:
            current_chick_cost_per_bird = round(float(chick_cost) / current_birds, 2)

        allocated_investor_birds = InvestorAllocation.objects.filter(
            batch=batch
        ).aggregate(total=Sum("birds_owned"))["total"] or 0

        admin_birds = batch.bird_count_initial - allocated_investor_birds

        investor_percentage = 0
        investor_birds = 0
        investor_current_birds = 0
        investor_mortality = 0
        investor_sold = 0
        investor_weight_sold = 0
        investor_chick_cost_share = 0
        investor_carriage_cost = 0
        investor_feed_cost = 0
        investor_medicine_cost = 0
        investor_cogs_share = 0
        investor_sales_revenue = 0
        investor_discount_share = 0
        investor_locked_cogs_total = 0
        investor_expense_share = 0

        admin_percentage = 0
        admin_current_birds = 0
        admin_mortality = 0
        admin_sold = 0
        admin_weight_sold = 0
        admin_chick_cost_share = 0
        admin_feed_cost_share = 0
        admin_medicine_cost_share = 0
        admin_carriage_cost_share = 0
        admin_cogs_share = 0
        admin_sales_revenue = 0
        admin_discount_share = 0
        admin_locked_cogs_total = 0
        admin_expense_share = 0

        investor_share_ratio = 0
        admin_share_ratio = 0

        if not is_admin and hasattr(request.user, "investor_profile"):
            allocation = InvestorAllocation.objects.filter(
                batch=batch,
                investor=request.user.investor_profile
            ).first()

            if allocation and batch.bird_count_initial > 0:
                investor_birds = allocation.birds_owned
                investor_share_ratio = investor_birds / batch.bird_count_initial

                investor_percentage = round(investor_share_ratio * 100, 1)
                investor_mortality = round(total_mortality * investor_share_ratio)
                investor_sold = math.floor(total_sold * investor_share_ratio)
                investor_current_birds = investor_birds - investor_mortality - investor_sold
                investor_weight_sold = round(total_sale_weight * investor_share_ratio, 2)

                investor_chick_cost_share = round(float(chick_cost) * investor_share_ratio, 2)
                investor_carriage_cost = round(float(carriage_cost) * investor_share_ratio, 2)
                investor_feed_cost = round(float(feed_cost) * investor_share_ratio, 2)
                investor_medicine_cost = round(float(medicine_cost) * investor_share_ratio, 2)
                investor_cogs_share = round(float(total_cogs) * investor_share_ratio, 2)

                investor_sales_revenue = round(total_sales_revenue * investor_share_ratio, 2)
                investor_discount_share = round(total_discount * investor_share_ratio, 2)
                investor_expense_share = round(float(total_expenses) * investor_share_ratio, 2)

        if is_admin and batch.bird_count_initial > 0 and admin_birds > 0:
            admin_share_ratio = admin_birds / batch.bird_count_initial
            investor_share_for_admin = allocated_investor_birds / batch.bird_count_initial

            admin_percentage = round(admin_share_ratio * 100, 1)

            investor_mortality_for_admin = round(total_mortality * investor_share_for_admin)
            investor_sold_for_admin = math.floor(total_sold * investor_share_for_admin)

            admin_mortality = total_mortality - investor_mortality_for_admin
            admin_sold = total_sold - investor_sold_for_admin
            admin_current_birds = admin_birds - admin_mortality - admin_sold

            investor_weight_for_admin = round(total_sale_weight * investor_share_for_admin, 2)
            admin_weight_sold = round(total_sale_weight - investor_weight_for_admin, 2)

            admin_chick_cost_share = round(float(chick_cost) * admin_share_ratio, 2)
            admin_carriage_cost_share = round(float(carriage_cost) * admin_share_ratio, 2)
            admin_feed_cost_share = round(float(feed_cost) * admin_share_ratio, 2)
            admin_medicine_cost_share = round(float(medicine_cost) * admin_share_ratio, 2)
            admin_cogs_share = round(float(total_cogs) * admin_share_ratio, 2)

            admin_sales_revenue = round(total_sales_revenue * admin_share_ratio, 2)
            admin_discount_share = round(total_discount * admin_share_ratio, 2)
            admin_expense_share = round(float(total_expenses) * admin_share_ratio, 2)

        sale_history = []

        for sale in sales_records:
            investor_sale_cogs = round(float(sale.cogs_allocated) * investor_share_ratio, 2)
            admin_sale_cogs = round(float(sale.cogs_allocated) * admin_share_ratio, 2)

            investor_locked_cogs_total += investor_sale_cogs
            admin_locked_cogs_total += admin_sale_cogs

            investor_gross_revenue = round(float(sale.gross_amount) * investor_share_ratio, 2)
            investor_discount = round(float(sale.discount_amount) * investor_share_ratio, 2)
            investor_net_revenue = round(float(sale.total_amount) * investor_share_ratio, 2)
            investor_profit = round(investor_net_revenue - investor_sale_cogs, 2)

            admin_gross_revenue = round(float(sale.gross_amount) * admin_share_ratio, 2)
            admin_discount = round(float(sale.discount_amount) * admin_share_ratio, 2)
            admin_net_revenue = round(float(sale.total_amount) * admin_share_ratio, 2)
            admin_profit = round(admin_net_revenue - admin_sale_cogs, 2)

            sale_history.append({
                "sale_date": sale.sale_date,
                "birds_sold": sale.birds_sold,
                "total_weight_kg": sale.total_weight_kg,
                "rate_per_kg": sale.rate_per_kg,
                "discount_amount": sale.discount_amount,
                "total_amount": sale.total_amount,
                "cogs_allocated": sale.cogs_allocated,
                "gross_profit": round(float(sale.total_amount) - float(sale.cogs_allocated), 2),

                "investor_birds_sold": math.floor(sale.birds_sold * investor_share_ratio),
                "investor_weight_sold": round(float(sale.total_weight_kg) * investor_share_ratio, 2),
                "investor_gross_revenue": investor_gross_revenue,
                "investor_discount": investor_discount,
                "investor_net_revenue": investor_net_revenue,
                "investor_cogs": investor_sale_cogs,
                "investor_profit": investor_profit,

                "admin_birds_sold": (
                    sale.birds_sold
                    - math.floor(sale.birds_sold * (allocated_investor_birds / batch.bird_count_initial))
                ) if batch.bird_count_initial > 0 else 0,
                "admin_weight_sold": round(float(sale.total_weight_kg) * admin_share_ratio, 2),
                "admin_gross_revenue": admin_gross_revenue,
                "admin_discount": admin_discount,
                "admin_net_revenue": admin_net_revenue,
                "admin_revenue": admin_net_revenue,
                "admin_cogs": admin_sale_cogs,
                "admin_profit": admin_profit,
            })

        investor_net_income = round(
            investor_sales_revenue - investor_locked_cogs_total - investor_expense_share,
            2
        )

        admin_net_income = round(
            admin_sales_revenue - admin_locked_cogs_total - admin_expense_share,
            2
        )

        batch_locked_cogs_total = sum(float(sale.cogs_allocated) for sale in sales_records)

        batch_net_income = round(
            total_sales_revenue - batch_locked_cogs_total - float(total_expenses),
            2
        )

        investor_total_investment = investor_cogs_share + investor_expense_share
        investor_roi = round(
            (investor_net_income / investor_total_investment) * 100,
            2
        ) if investor_total_investment > 0 else 0

        admin_total_investment = admin_cogs_share + admin_expense_share
        admin_roi = round(
            (admin_net_income / admin_total_investment) * 100,
            2
        ) if admin_total_investment > 0 else 0

        batch_total_investment = float(total_cogs) + float(total_expenses)
        batch_roi = round(
            (batch_net_income / batch_total_investment) * 100,
            2
        ) if batch_total_investment > 0 else 0

        expense_history = []

        for expense in expenses.order_by("-expense_date", "-id"):
            full_amount = float(expense.amount)

            expense_history.append({
                "expense_date": expense.expense_date,
                "category": expense.get_category_display(),
                "description": expense.description,
                "amount": full_amount,
                "investor_share_amount": round(full_amount * investor_share_ratio, 2),
                "admin_share_amount": round(full_amount * admin_share_ratio, 2),
            })

        finance_rows.append({
            "batch": batch,
            "current_birds": current_birds,
            "total_mortality": total_mortality,
            "total_sold": total_sold,
            "total_discount": round(total_discount, 2),
            "gross_sales_revenue": round(gross_sales_revenue, 2),
            "total_sales_revenue": round(total_sales_revenue, 2),
            "total_sale_weight": round(total_sale_weight, 2),
            "average_sale_weight": average_sale_weight,
            "average_sale_rate": average_sale_rate,

            "chick_cost": chick_cost,
            "carriage_cost": carriage_cost,
            "feed_cost": feed_cost,
            "medicine_cost": medicine_cost,
            "total_cogs": total_cogs,
            "current_chick_cost_per_bird": current_chick_cost_per_bird,

            "investor_percentage": investor_percentage,
            "investor_birds": investor_birds,
            "investor_current_birds": investor_current_birds,
            "investor_mortality": investor_mortality,
            "investor_sold": investor_sold,
            "investor_weight_sold": investor_weight_sold,
            "investor_chick_cost_share": investor_chick_cost_share,
            "investor_carriage_cost": investor_carriage_cost,
            "investor_feed_cost": investor_feed_cost,
            "investor_medicine_cost": investor_medicine_cost,
            "investor_cogs_share": investor_cogs_share,
            "investor_sales_revenue": investor_sales_revenue,
            "investor_discount_share": investor_discount_share,
            "investor_locked_cogs_total": round(investor_locked_cogs_total, 2),
            "investor_expense_share": investor_expense_share,

            "admin_birds": admin_birds,
            "admin_percentage": admin_percentage,
            "admin_current_birds": admin_current_birds,
            "admin_mortality": admin_mortality,
            "admin_sold": admin_sold,
            "admin_weight_sold": admin_weight_sold,
            "admin_chick_cost_share": admin_chick_cost_share,
            "admin_feed_cost_share": admin_feed_cost_share,
            "admin_medicine_cost_share": admin_medicine_cost_share,
            "admin_carriage_cost_share": admin_carriage_cost_share,
            "admin_cogs_share": admin_cogs_share,
            "admin_sales_revenue": admin_sales_revenue,
            "admin_discount_share": admin_discount_share,
            "admin_locked_cogs_total": round(admin_locked_cogs_total, 2),
            "admin_expense_share": admin_expense_share,

            "sale_history": sale_history,
            "total_expenses": total_expenses,
            "expense_history": expense_history,

            "investor_net_income": investor_net_income,
            "admin_net_income": admin_net_income,
            "batch_locked_cogs_total": round(batch_locked_cogs_total, 2),
            "batch_net_income": batch_net_income,

            "investor_roi": investor_roi,
            "admin_roi": admin_roi,
            "batch_roi": batch_roi,

            "investor_total_investment": investor_total_investment,
            "admin_total_investment": admin_total_investment,
        })

    return render(request, "api/finance_tracker.html", {
        "finance_rows": finance_rows,
        "is_admin": is_admin,
    })


def attach_current_birds(batches):
    for batch in batches:
        total_mortality = MortalityRecord.objects.filter(
            batch=batch
        ).aggregate(total=Sum("count"))["total"] or 0

        total_sold = SaleRecord.objects.filter(
            batch=batch
        ).aggregate(total=Sum("birds_sold"))["total"] or 0

        batch.current_birds = batch.bird_count_initial - total_mortality - total_sold

    return batches


@login_required
@require_http_methods(["GET", "POST"])
def add_sale_record(request):
    is_admin = request.user.is_superuser or request.user.is_staff

    if not is_admin:
        messages.error(request, "Only admin can add sales.")
        return redirect("dashboard")

    batches = Batch.objects.filter(
        is_active=True,
        status="active"
    ).order_by("-start_date", "batch_number")

    batches = attach_current_birds(batches)

    if request.method == "POST":
        batch_id = request.POST.get("batch_id")
        batch = get_object_or_404(Batch, id=batch_id)

        if batch.status == "closed" or not batch.is_active:
            messages.error(request, "This batch is closed.")
            return redirect("finance_tracker")

        sale_date = request.POST.get("sale_date")
        birds_sold = int(request.POST.get("birds_sold") or 0)
        total_weight_kg = Decimal(request.POST.get("total_weight_kg") or "0")
        rate_per_kg = Decimal(request.POST.get("rate_per_kg") or "0")
        discount_amount = Decimal(request.POST.get("discount_amount") or "0")
        notes = request.POST.get("notes", "")

        total_mortality = MortalityRecord.objects.filter(batch=batch).aggregate(
            total=Sum("count")
        )["total"] or 0

        previous_sold = SaleRecord.objects.filter(batch=batch).aggregate(
            total=Sum("birds_sold")
        )["total"] or 0

        current_birds_before_sale = batch.bird_count_initial - total_mortality - previous_sold

        if birds_sold <= 0:
            messages.error(request, "Birds sold must be greater than zero.")
            return redirect("add_sale_record")

        if birds_sold > current_birds_before_sale:
            messages.error(request, "Birds sold cannot be more than current available birds.")
            return redirect("add_sale_record")

        chick_cost = ChickCostEntry.objects.filter(batch=batch).aggregate(
            total=Sum("chick_cost")
        )["total"] or Decimal("0")

        carriage_cost = ChickCostEntry.objects.filter(batch=batch).aggregate(
            total=Sum("carriage_cost")
        )["total"] or Decimal("0")

        feed_cost = FeedEntry.objects.filter(batch=batch).aggregate(
            total=Sum("amount")
        )["total"] or Decimal("0")

        medicine_cost = MedicineEntry.objects.filter(batch=batch).aggregate(
            total=Sum("amount")
        )["total"] or Decimal("0")

        total_cogs = chick_cost + carriage_cost + feed_cost + medicine_cost

        previous_locked_cogs = SaleRecord.objects.filter(batch=batch).aggregate(
            total=Sum("cogs_allocated")
        )["total"] or Decimal("0")

        remaining_cogs_before_sale = Decimal(total_cogs) - Decimal(previous_locked_cogs)

        cogs_per_bird_at_sale = Decimal("0")
        if current_birds_before_sale > 0:
            cogs_per_bird_at_sale = remaining_cogs_before_sale / Decimal(current_birds_before_sale)

        cogs_allocated = cogs_per_bird_at_sale * Decimal(birds_sold)

        gross_amount = total_weight_kg * rate_per_kg
        net_sale_amount = gross_amount - discount_amount
        gross_profit = net_sale_amount - cogs_allocated

        SaleRecord.objects.create(
            batch=batch,
            sale_date=sale_date,
            birds_sold=birds_sold,
            total_weight_kg=total_weight_kg,
            rate_per_kg=rate_per_kg,
            discount_amount=discount_amount,
            notes=notes,
            cogs_per_bird_at_sale=cogs_per_bird_at_sale.quantize(Decimal("0.01")),
            cogs_allocated=cogs_allocated.quantize(Decimal("0.01")),
            gross_profit=gross_profit.quantize(Decimal("0.01")),
        )

        messages.success(request, "Sale recorded and COGS locked successfully.")
        return redirect("finance_tracker")

    return render(request, "api/add_sale_record.html", {
        "batches": batches,
    })

@login_required
def feed_list(request):
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
        messages.error(request, "You are not allowed to view feed entries.")
        return redirect("dashboard")

    feed_groups = []

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

        feed_entries = FeedEntry.objects.filter(
            batch=batch
        ).order_by("-entry_date", "-id")

        feed_rows = []

        for entry in feed_entries:
            amount = float(entry.amount)

            if not is_admin:
                amount = round(amount * share_ratio, 2)

            feed_rows.append({
                "entry_date": entry.entry_date,
                "notes": entry.notes,
                "amount": amount,
            })

        total_feed = sum(row["amount"] for row in feed_rows)

        feed_groups.append({
            "batch": batch,
            "feed_entries": feed_rows,
            "total_feed": total_feed,
        })

    return render(request, "api/feed_list.html", {
        "feed_groups": feed_groups,
        "is_admin": is_admin,
    })

@login_required
@require_http_methods(["GET", "POST"])
def add_feed_entry(request):
    is_admin = request.user.is_superuser or request.user.is_staff

    if not is_admin:
        messages.error(request, "Only admin can add feed entries.")
        return redirect("dashboard")

    batches = Batch.objects.filter(
        is_active=True,
        status="active"
    ).order_by("-start_date", "batch_number")


    batches = attach_current_birds(batches)

    if request.method == "POST":
        batch_id = request.POST.get("batch_id")
        batch = get_object_or_404(Batch, id=batch_id)



        if batch.status == "closed" or not batch.is_active:
            messages.error(request, "This batch is closed. You cannot add feed.")
            return redirect("finance_tracker")

        entry_date = request.POST.get("entry_date") or timezone.now().date()
        amount = request.POST.get("amount") or 0
        notes = request.POST.get("notes", "")

        FeedEntry.objects.create(
            batch=batch,
            entry_date=entry_date,
            amount=amount,
            notes=notes,
        )

        messages.success(request, "Feed entry added successfully.")
        return redirect("finance_tracker")

    return render(request, "api/add_feed_entry.html", {
        "batches": batches,
    })


@login_required
def medicine_list(request):
    is_admin = request.user.is_superuser or request.user.is_staff

    if not is_admin:
        messages.error(request, "Only admin can view medicine entries.")
        return redirect("dashboard")

    batches = Batch.objects.filter(
        is_active=True,
        status="active"
    ).order_by("-start_date", "batch_number")

    medicine_groups = []

    for batch in batches:
        medicine_entries = MedicineEntry.objects.filter(
            batch=batch
        ).order_by("-entry_date", "-id")

        total_medicine = sum(entry.amount for entry in medicine_entries)

        medicine_groups.append({
            "batch": batch,
            "medicine_entries": medicine_entries,
            "total_medicine": total_medicine,
        })

    return render(request, "api/medicine_list.html", {
        "medicine_groups": medicine_groups,
    })


@login_required
@require_http_methods(["GET", "POST"])
def add_medicine_entry(request):
    is_admin = request.user.is_superuser or request.user.is_staff

    if not is_admin:
        messages.error(request, "Only admin can add medicine entries.")
        return redirect("dashboard")

    batches = Batch.objects.filter(
        is_active=True,
        status="active"
    ).order_by("-start_date", "batch_number")

    batches = attach_current_birds(batches)

    if request.method == "POST":
        batch_id = request.POST.get("batch_id")
        batch = get_object_or_404(Batch, id=batch_id)

        if batch.status == "closed" or not batch.is_active:
            messages.error(request, "This batch is closed.")
            return redirect("finance_tracker")

        entry_date = request.POST.get("entry_date")
        amount = request.POST.get("amount")
        notes = request.POST.get("notes", "")

        MedicineEntry.objects.create(
            batch=batch,
            entry_date=entry_date,
            amount=amount,
            notes=notes,
        )

        messages.success(request, "Medicine entry added successfully.")
        return redirect("finance_tracker")

    return render(
        request,
        "api/add_medicine_entry.html",
        {"batches": batches},
    )


@login_required
@require_http_methods(["GET", "POST"])
def add_chick_cost(request):
    is_admin = request.user.is_superuser or request.user.is_staff

    if not is_admin:
        messages.error(request, "Only admin can add chick cost.")
        return redirect("dashboard")

    batches = Batch.objects.filter(
        is_active=True,
        status="active"
    ).order_by("-start_date", "batch_number")

    batches = attach_current_birds(batches)

    if request.method == "POST":
        batch_id = request.POST.get("batch_id")
        batch = get_object_or_404(Batch, id=batch_id)

        if batch.status == "closed" or not batch.is_active:
            messages.error(request, "This batch is closed.")
            return redirect("finance_tracker")

        entry_date = request.POST.get("entry_date") or timezone.now().date()
        chick_cost = request.POST.get("chick_cost") or 0
        carriage_cost = request.POST.get("carriage_cost") or 0
        notes = request.POST.get("notes", "")

        ChickCostEntry.objects.create(
            batch=batch,
            entry_date=entry_date,
            chick_cost=chick_cost,
            carriage_cost=carriage_cost,
            notes=notes,
        )

        messages.success(request, "Chick and carriage cost added successfully.")
        return redirect("finance_tracker")

    return render(request, "api/add_chick_cost.html", {
        "batches": batches,
    })


@login_required
def batch_report(request):
    is_admin = request.user.is_superuser or request.user.is_staff
    is_investor = hasattr(request.user, "investor_profile")

    if is_admin:
        batches = Batch.objects.filter(is_active=True).order_by("-start_date", "batch_number")
    elif is_investor:
        investor_batch_ids = InvestorAllocation.objects.filter(
            investor=request.user.investor_profile
        ).values_list("batch_id", flat=True)

        batches = Batch.objects.filter(
            id__in=investor_batch_ids,
            is_active=True
        ).order_by("-start_date", "batch_number")
    else:
        messages.error(request, "You are not allowed to view reports.")
        return redirect("dashboard")

    report_rows = []

    for batch in batches:
        total_mortality = MortalityRecord.objects.filter(batch=batch).aggregate(
            total=Sum("count")
        )["total"] or 0

        sales = SaleRecord.objects.filter(batch=batch)

        total_sold = sales.aggregate(total=Sum("birds_sold"))["total"] or 0
        total_revenue = sum(float(sale.total_amount) for sale in sales)
        total_weight = sum(float(sale.total_weight_kg) for sale in sales)
        locked_cogs = sum(float(sale.cogs_allocated) for sale in sales)

        chick_cost = ChickCostEntry.objects.filter(batch=batch).aggregate(
            total=Sum("chick_cost")
        )["total"] or 0

        carriage_cost = ChickCostEntry.objects.filter(batch=batch).aggregate(
            total=Sum("carriage_cost")
        )["total"] or 0

        feed_cost = FeedEntry.objects.filter(batch=batch).aggregate(
            total=Sum("amount")
        )["total"] or 0

        medicine_cost = MedicineEntry.objects.filter(batch=batch).aggregate(
            total=Sum("amount")
        )["total"] or 0

        expenses = Expense.objects.filter(batch=batch).aggregate(
            total=Sum("amount")
        )["total"] or 0

        total_cogs = float(chick_cost + carriage_cost + feed_cost + medicine_cost)
        current_birds = batch.bird_count_initial - total_mortality - total_sold

        allocated_investor_birds = InvestorAllocation.objects.filter(
            batch=batch
        ).aggregate(total=Sum("birds_owned"))["total"] or 0

        admin_birds = batch.bird_count_initial - allocated_investor_birds

        if is_admin:
            share_ratio = admin_birds / batch.bird_count_initial if batch.bird_count_initial > 0 else 0
            report_title = "Admin Share Report"
            starting_birds = admin_birds
        else:
            allocation = InvestorAllocation.objects.filter(
                batch=batch,
                investor=request.user.investor_profile
            ).first()

            share_ratio = allocation.birds_owned / batch.bird_count_initial if allocation and batch.bird_count_initial > 0 else 0
            report_title = "Investor Share Report"
            starting_birds = allocation.birds_owned if allocation else 0

        share_mortality = round(total_mortality * share_ratio)
        share_sold = math.floor(total_sold * share_ratio)
        share_current_birds = starting_birds - share_mortality - share_sold

        share_revenue = round(total_revenue * share_ratio, 2)
        share_locked_cogs = round(locked_cogs * share_ratio, 2)
        share_expenses = round(float(expenses) * share_ratio, 2)
        share_net_income = round(share_revenue - share_locked_cogs - share_expenses, 2)

        share_total_investment = round((total_cogs * share_ratio) + share_expenses, 2)

        share_roi = round(
            (share_net_income / share_total_investment) * 100,
            2
        ) if share_total_investment > 0 else 0

        batch_net_income = round(total_revenue - locked_cogs - float(expenses), 2)

        batch_roi = round(
            (batch_net_income / (total_cogs + float(expenses))) * 100,
            2
        ) if (total_cogs + float(expenses)) > 0 else 0

        report_rows.append({
            "batch": batch,
            "report_title": report_title,
            "share_percentage": round(share_ratio * 100, 1),

            "starting_birds": starting_birds,
            "current_birds": share_current_birds,
            "mortality": share_mortality,
            "sold": share_sold,

            "revenue": share_revenue,
            "locked_cogs": share_locked_cogs,
            "expenses": share_expenses,
            "net_income": share_net_income,
            "roi": share_roi,

            "total_batch_start": batch.bird_count_initial,
            "total_batch_current": current_birds,
            "total_batch_mortality": total_mortality,
            "total_batch_sold": total_sold,
            "total_batch_revenue": round(total_revenue, 2),
            "total_batch_cogs": round(locked_cogs, 2),
            "total_batch_expenses": round(float(expenses), 2),
            "total_batch_net_income": batch_net_income,
            "total_batch_roi": batch_roi,

            "total_weight": round(total_weight * share_ratio, 2),
        })

    return render(request, "api/batch_report.html", {
        "report_rows": report_rows,
        "is_admin": is_admin,
    })


