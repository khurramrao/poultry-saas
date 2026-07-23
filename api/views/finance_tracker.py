from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.contrib import messages
from django.views.decorators.http import require_http_methods


from decimal import Decimal
import math

from api.models.sensor import Batch, MortalityRecord
from api.models.sales import ChickCostEntry, SaleRecord, Expense
from api.models.investors import InvestorAllocation, FeedEntry, MedicineEntry

from io import BytesIO
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count, Q
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import inch
from pathlib import Path
from django.conf import settings
from reportlab.platypus import Image
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Image,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

from django.contrib.staticfiles import finders
from reportlab.platypus import Image





@login_required
def finance_tracker(request):

    is_admin = request.user.is_superuser or request.user.is_staff

    # =========================================================
    # GET BATCHES
    # =========================================================

    if is_admin:
        batches = Batch.objects.filter().order_by(
            "-is_active",
            "-start_date"
        )

    else:
        if not hasattr(request.user, "investor_profile"):
            return redirect("dashboard")

        investor_batch_ids = InvestorAllocation.objects.filter(
            investor=request.user.investor_profile
        ).values_list("batch_id", flat=True)

        batches = Batch.objects.filter(
            id__in=investor_batch_ids
        ).order_by(
            "-is_active",
            "-start_date"
        )

    finance_rows = []

    # =========================================================
    # PROCESS EACH BATCH
    # =========================================================

    for batch in batches:

        # -----------------------------------------------------
        # MORTALITY
        # -----------------------------------------------------

        total_mortality = MortalityRecord.objects.filter(
            batch=batch
        ).aggregate(
            total=Sum("count")
        )["total"] or 0

        # -----------------------------------------------------
        # SALES
        # -----------------------------------------------------

        sales_records = SaleRecord.objects.filter(
            batch=batch
        ).order_by(
            "-sale_date",
            "-id"
        )

        total_sold = sales_records.aggregate(
            total=Sum("birds_sold")
        )["total"] or 0

        total_sales_revenue = sum(
            float(sale.total_amount)
            for sale in sales_records
        )

        total_discount = sum(
            float(sale.discount_amount)
            for sale in sales_records
        )

        total_sale_weight = sum(
            float(sale.total_weight_kg)
            for sale in sales_records
        )

        gross_sales_revenue = sum(
            float(sale.gross_amount)
            for sale in sales_records
        )

        average_sale_weight = (
            round(total_sale_weight / total_sold, 3)
            if total_sold > 0
            else 0
        )

        average_sale_rate = (
            round(gross_sales_revenue / total_sale_weight, 2)
            if total_sale_weight > 0
            else 0
        )

        # -----------------------------------------------------
        # CURRENT BIRDS
        # -----------------------------------------------------

        current_birds = (
            batch.bird_count_initial
            - total_mortality
            - total_sold
        )

        # =====================================================
        # EXPENSES
        # =====================================================

        expenses = Expense.objects.filter(batch=batch)

        # Electricity is part of COGS
        electricity_expenses = expenses.filter(
            category="electricity"
        )

        # Other expenses remain operating expenses
        other_expenses = expenses.exclude(
            category="electricity"
        )

        electricity_cost = electricity_expenses.aggregate(
            total=Sum("amount")
        )["total"] or 0

        total_expenses = other_expenses.aggregate(
            total=Sum("amount")
        )["total"] or 0

        # =====================================================
        # COGS
        # =====================================================

        chick_cost = ChickCostEntry.objects.filter(
            batch=batch
        ).aggregate(
            total=Sum("chick_cost")
        )["total"] or 0

        carriage_cost = ChickCostEntry.objects.filter(
            batch=batch
        ).aggregate(
            total=Sum("carriage_cost")
        )["total"] or 0

        feed_cost = FeedEntry.objects.filter(
            batch=batch
        ).aggregate(
            total=Sum("amount")
        )["total"] or 0

        medicine_cost = MedicineEntry.objects.filter(
            batch=batch
        ).aggregate(
            total=Sum("amount")
        )["total"] or 0

        # Electricity is included here only once
        total_cogs = (
            chick_cost
            + carriage_cost
            + feed_cost
            + medicine_cost
            + electricity_cost
        )

        current_chick_cost_per_bird = 0

        if current_birds > 0:
            current_chick_cost_per_bird = round(
                float(chick_cost) / current_birds,
                2
            )

        # =====================================================
        # OWNERSHIP
        # =====================================================

        allocated_investor_birds = InvestorAllocation.objects.filter(
            batch=batch
        ).aggregate(
            total=Sum("birds_owned")
        )["total"] or 0

        admin_birds = (
            batch.bird_count_initial
            - allocated_investor_birds
        )

        # =====================================================
        # DEFAULT INVESTOR VALUES
        # =====================================================

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
        investor_electricity_cost = 0
        investor_cogs_share = 0

        investor_sales_revenue = 0
        investor_discount_share = 0
        investor_locked_cogs_total = 0
        investor_expense_share = 0
        investor_realized_expenses = 0

        # =====================================================
        # DEFAULT ADMIN VALUES
        # =====================================================

        admin_percentage = 0
        admin_current_birds = 0
        admin_mortality = 0
        admin_sold = 0
        admin_weight_sold = 0

        admin_chick_cost_share = 0
        admin_carriage_cost_share = 0
        admin_feed_cost_share = 0
        admin_medicine_cost_share = 0
        admin_electricity_cost_share = 0
        admin_cogs_share = 0

        admin_sales_revenue = 0
        admin_discount_share = 0
        admin_locked_cogs_total = 0
        admin_expense_share = 0
        admin_realized_expenses = 0

        investor_share_ratio = 0
        admin_share_ratio = 0

        # =====================================================
        # INVESTOR CALCULATION
        # =====================================================

        if (
            not is_admin
            and hasattr(request.user, "investor_profile")
        ):

            allocation = InvestorAllocation.objects.filter(
                batch=batch,
                investor=request.user.investor_profile
            ).first()

            if allocation and batch.bird_count_initial > 0:

                investor_birds = allocation.birds_owned

                investor_share_ratio = (
                    investor_birds
                    / batch.bird_count_initial
                )

                investor_percentage = round(
                    investor_share_ratio * 100,
                    1
                )

                investor_mortality = round(
                    total_mortality * investor_share_ratio
                )

                investor_sold = math.floor(
                    total_sold * investor_share_ratio
                )

                investor_current_birds = (
                    investor_birds
                    - investor_mortality
                    - investor_sold
                )

                investor_weight_sold = round(
                    total_sale_weight * investor_share_ratio,
                    2
                )

                investor_chick_cost_share = round(
                    float(chick_cost) * investor_share_ratio,
                    2
                )

                investor_carriage_cost = round(
                    float(carriage_cost) * investor_share_ratio,
                    2
                )

                investor_feed_cost = round(
                    float(feed_cost) * investor_share_ratio,
                    2
                )

                investor_medicine_cost = round(
                    float(medicine_cost) * investor_share_ratio,
                    2
                )

                investor_electricity_cost = round(
                    float(electricity_cost) * investor_share_ratio,
                    2
                )

                investor_cogs_share = round(
                    float(total_cogs) * investor_share_ratio,
                    2
                )

                investor_sales_revenue = round(
                    total_sales_revenue * investor_share_ratio,
                    2
                )

                investor_discount_share = round(
                    total_discount * investor_share_ratio,
                    2
                )

                investor_expense_share = round(
                    float(total_expenses) * investor_share_ratio,
                    2
                )

        # =====================================================
        # ADMIN CALCULATION
        # =====================================================

        if (
            is_admin
            and batch.bird_count_initial > 0
            and admin_birds > 0
        ):

            admin_share_ratio = (
                admin_birds
                / batch.bird_count_initial
            )

            investor_share_for_admin = (
                allocated_investor_birds
                / batch.bird_count_initial
            )

            admin_percentage = round(
                admin_share_ratio * 100,
                1
            )

            investor_mortality_for_admin = round(
                total_mortality
                * investor_share_for_admin
            )

            investor_sold_for_admin = math.floor(
                total_sold
                * investor_share_for_admin
            )

            admin_mortality = (
                total_mortality
                - investor_mortality_for_admin
            )

            admin_sold = (
                total_sold
                - investor_sold_for_admin
            )

            admin_current_birds = (
                admin_birds
                - admin_mortality
                - admin_sold
            )

            investor_weight_for_admin = round(
                total_sale_weight
                * investor_share_for_admin,
                2
            )

            admin_weight_sold = round(
                total_sale_weight
                - investor_weight_for_admin,
                2
            )

            admin_chick_cost_share = round(
                float(chick_cost) * admin_share_ratio,
                2
            )

            admin_carriage_cost_share = round(
                float(carriage_cost) * admin_share_ratio,
                2
            )

            admin_feed_cost_share = round(
                float(feed_cost) * admin_share_ratio,
                2
            )

            admin_medicine_cost_share = round(
                float(medicine_cost) * admin_share_ratio,
                2
            )

            admin_electricity_cost_share = round(
                float(electricity_cost) * admin_share_ratio,
                2
            )

            admin_cogs_share = round(
                float(total_cogs) * admin_share_ratio,
                2
            )

            admin_sales_revenue = round(
                total_sales_revenue * admin_share_ratio,
                2
            )

            admin_discount_share = round(
                total_discount * admin_share_ratio,
                2
            )

            admin_expense_share = round(
                float(total_expenses) * admin_share_ratio,
                2
            )

        # =====================================================
        # SALE HISTORY AND LOCKED COGS
        # =====================================================

        sale_history = []

        for sale in sales_records:

            investor_sale_cogs = round(
                float(sale.cogs_allocated)
                * investor_share_ratio,
                2
            )

            admin_sale_cogs = round(
                float(sale.cogs_allocated)
                * admin_share_ratio,
                2
            )

            investor_locked_cogs_total += investor_sale_cogs
            admin_locked_cogs_total += admin_sale_cogs

            investor_gross_revenue = round(
                float(sale.gross_amount)
                * investor_share_ratio,
                2
            )

            investor_discount = round(
                float(sale.discount_amount)
                * investor_share_ratio,
                2
            )

            investor_net_revenue = round(
                float(sale.total_amount)
                * investor_share_ratio,
                2
            )

            investor_profit = round(
                investor_net_revenue
                - investor_sale_cogs,
                2
            )

            admin_gross_revenue = round(
                float(sale.gross_amount)
                * admin_share_ratio,
                2
            )

            admin_discount = round(
                float(sale.discount_amount)
                * admin_share_ratio,
                2
            )

            admin_net_revenue = round(
                float(sale.total_amount)
                * admin_share_ratio,
                2
            )

            admin_profit = round(
                admin_net_revenue
                - admin_sale_cogs,
                2
            )

            sale_history.append({

                "sale_date": sale.sale_date,
                "birds_sold": sale.birds_sold,
                "total_weight_kg": sale.total_weight_kg,
                "rate_per_kg": sale.rate_per_kg,
                "discount_amount": sale.discount_amount,
                "total_amount": sale.total_amount,
                "cogs_allocated": sale.cogs_allocated,

                "gross_profit": round(
                    float(sale.total_amount)
                    - float(sale.cogs_allocated),
                    2
                ),

                "investor_birds_sold": math.floor(
                    sale.birds_sold
                    * investor_share_ratio
                ),

                "investor_weight_sold": round(
                    float(sale.total_weight_kg)
                    * investor_share_ratio,
                    2
                ),

                "investor_gross_revenue": investor_gross_revenue,
                "investor_discount": investor_discount,
                "investor_net_revenue": investor_net_revenue,
                "investor_cogs": investor_sale_cogs,
                "investor_profit": investor_profit,

                "admin_birds_sold": (
                    sale.birds_sold
                    - math.floor(
                        sale.birds_sold
                        * (
                            allocated_investor_birds
                            / batch.bird_count_initial
                        )
                    )
                ) if batch.bird_count_initial > 0 else 0,

                "admin_weight_sold": round(
                    float(sale.total_weight_kg)
                    * admin_share_ratio,
                    2
                ),

                "admin_gross_revenue": admin_gross_revenue,
                "admin_discount": admin_discount,
                "admin_net_revenue": admin_net_revenue,
                "admin_revenue": admin_net_revenue,
                "admin_cogs": admin_sale_cogs,
                "admin_profit": admin_profit,
            })

        investor_locked_cogs_total = round(
            investor_locked_cogs_total,
            2
        )

        admin_locked_cogs_total = round(
            admin_locked_cogs_total,
            2
        )

        batch_locked_cogs_total = round(
            sum(
                float(sale.cogs_allocated)
                for sale in sales_records
            ),
            2
        )

        # =====================================================
        # ALLOCATE OTHER EXPENSES TO SOLD BIRDS
        #
        # Electricity is not included here because electricity
        # is already included in COGS.
        #
        # Mortality cost remains with surviving/sold birds.
        # Therefore:
        # allocation birds = current birds + sold birds
        # =====================================================

        expense_allocation_birds = (
            current_birds
            + total_sold
        )

        sold_expense_ratio = 0

        if expense_allocation_birds > 0:
            sold_expense_ratio = (
                total_sold
                / expense_allocation_birds
            )

        batch_realized_expenses = round(
            float(total_expenses)
            * sold_expense_ratio,
            2
        )

        investor_realized_expenses = round(
            batch_realized_expenses
            * investor_share_ratio,
            2
        )

        admin_realized_expenses = round(
            batch_realized_expenses
            * admin_share_ratio,
            2
        )

        # =====================================================
        # BATCH REALIZED ROI
        # =====================================================

        batch_net_income = round(
            total_sales_revenue
            - batch_locked_cogs_total
            - batch_realized_expenses,
            2
        )

        batch_total_investment = round(
            batch_locked_cogs_total
            + batch_realized_expenses,
            2
        )

        batch_roi = 0

        if batch_total_investment > 0:
            batch_roi = round(
                (
                    batch_net_income
                    / batch_total_investment
                ) * 100,
                2
            )

        # =====================================================
        # INVESTOR REALIZED ROI
        # =====================================================

        investor_net_income = round(
            investor_sales_revenue
            - investor_locked_cogs_total
            - investor_realized_expenses,
            2
        )

        investor_total_investment = round(
            investor_locked_cogs_total
            + investor_realized_expenses,
            2
        )

        investor_roi = 0

        if investor_total_investment > 0:
            investor_roi = round(
                (
                    investor_net_income
                    / investor_total_investment
                ) * 100,
                2
            )

        # =====================================================
        # ADMIN REALIZED ROI
        # =====================================================

        admin_net_income = round(
            admin_sales_revenue
            - admin_locked_cogs_total
            - admin_realized_expenses,
            2
        )

        admin_total_investment = round(
            admin_locked_cogs_total
            + admin_realized_expenses,
            2
        )

        admin_roi = 0

        if admin_total_investment > 0:
            admin_roi = round(
                (
                    admin_net_income
                    / admin_total_investment
                ) * 100,
                2
            )

        # =====================================================
        # EXPENSE HISTORY
        # Electricity will not appear here because it is COGS
        # =====================================================

        expense_history = []

        for expense in other_expenses.order_by(
            "-expense_date",
            "-id"
        ):

            full_amount = float(expense.amount)

            expense_history.append({

                "expense_date": expense.expense_date,
                "category": expense.get_category_display(),
                "description": expense.description,
                "amount": full_amount,

                "investor_share_amount": round(
                    full_amount
                    * investor_share_ratio,
                    2
                ),

                "admin_share_amount": round(
                    full_amount
                    * admin_share_ratio,
                    2
                ),
            })

        # =====================================================
        # SEND DATA TO TEMPLATE
        # =====================================================

        finance_rows.append({

            "batch": batch,

            # Birds
            "current_birds": current_birds,
            "total_mortality": total_mortality,
            "total_sold": total_sold,

            # Sales
            "total_discount": round(total_discount, 2),
            "gross_sales_revenue": round(
                gross_sales_revenue,
                2
            ),
            "total_sales_revenue": round(
                total_sales_revenue,
                2
            ),
            "total_sale_weight": round(
                total_sale_weight,
                2
            ),
            "average_sale_weight": average_sale_weight,
            "average_sale_rate": average_sale_rate,

            # COGS
            "chick_cost": chick_cost,
            "carriage_cost": carriage_cost,
            "feed_cost": feed_cost,
            "medicine_cost": medicine_cost,
            "electricity_cost": electricity_cost,
            "total_cogs": total_cogs,
            "current_chick_cost_per_bird": (
                current_chick_cost_per_bird
            ),

            # Investor
            "investor_percentage": investor_percentage,
            "investor_birds": investor_birds,
            "investor_current_birds": investor_current_birds,
            "investor_mortality": investor_mortality,
            "investor_sold": investor_sold,
            "investor_weight_sold": investor_weight_sold,

            "investor_chick_cost_share": (
                investor_chick_cost_share
            ),
            "investor_carriage_cost": (
                investor_carriage_cost
            ),
            "investor_feed_cost": investor_feed_cost,
            "investor_medicine_cost": (
                investor_medicine_cost
            ),
            "investor_electricity_cost": (
                investor_electricity_cost
            ),
            "investor_cogs_share": investor_cogs_share,

            "investor_sales_revenue": (
                investor_sales_revenue
            ),
            "investor_discount_share": (
                investor_discount_share
            ),
            "investor_locked_cogs_total": (
                investor_locked_cogs_total
            ),
            "investor_expense_share": (
                investor_expense_share
            ),
            "investor_realized_expenses": (
                investor_realized_expenses
            ),
            "investor_net_income": investor_net_income,
            "investor_total_investment": (
                investor_total_investment
            ),
            "investor_roi": investor_roi,

            # Admin
            "admin_birds": admin_birds,
            "admin_percentage": admin_percentage,
            "admin_current_birds": admin_current_birds,
            "admin_mortality": admin_mortality,
            "admin_sold": admin_sold,
            "admin_weight_sold": admin_weight_sold,

            "admin_chick_cost_share": (
                admin_chick_cost_share
            ),
            "admin_carriage_cost_share": (
                admin_carriage_cost_share
            ),
            "admin_feed_cost_share": (
                admin_feed_cost_share
            ),
            "admin_medicine_cost_share": (
                admin_medicine_cost_share
            ),
            "admin_electricity_cost_share": (
                admin_electricity_cost_share
            ),
            "admin_cogs_share": admin_cogs_share,

            "admin_sales_revenue": admin_sales_revenue,
            "admin_discount_share": admin_discount_share,
            "admin_locked_cogs_total": (
                admin_locked_cogs_total
            ),
            "admin_expense_share": admin_expense_share,
            "admin_realized_expenses": (
                admin_realized_expenses
            ),
            "admin_net_income": admin_net_income,
            "admin_total_investment": (
                admin_total_investment
            ),
            "admin_roi": admin_roi,

            # Batch ROI
            "batch_locked_cogs_total": (
                batch_locked_cogs_total
            ),
            "batch_realized_expenses": (
                batch_realized_expenses
            ),
            "batch_net_income": batch_net_income,
            "batch_total_investment": (
                batch_total_investment
            ),
            "batch_roi": batch_roi,

            # Histories
            "sale_history": sale_history,
            "total_expenses": total_expenses,
            "expense_history": expense_history,
        })

    # =========================================================
    # RENDER PAGE
    # =========================================================

    return render(
        request,
        "api/finance_tracker.html",
        {
            "finance_rows": finance_rows,
            "is_admin": is_admin,
        }
    )


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

        electricity_cost = Expense.objects.filter(
            batch=batch,
            category="electricity"
        ).aggregate(
            total=Sum("amount")
        )["total"] or Decimal("0")

        total_cogs = (
                chick_cost
                + carriage_cost
                + feed_cost
                + medicine_cost
                + electricity_cost
        )

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
        messages.error(request, "You are not allowed to view medicine entries.")
        return redirect("dashboard")

    medicine_groups = []

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

        medicine_entries = MedicineEntry.objects.filter(
            batch=batch
        ).order_by("-entry_date", "-id")

        medicine_rows = []

        for entry in medicine_entries:
            amount = float(entry.amount)

            # Investor sees only their ownership share
            if not is_admin:
                amount = round(amount * share_ratio, 2)

            medicine_rows.append({
                "entry_date": entry.entry_date,
                "medicine_name": entry.medicine_name,
                "medicine_type": entry.medicine_type,
                "medicine_type_display": entry.get_medicine_type_display(),
                "notes": entry.notes,
                "amount": amount,
            })

        total_medicine = sum(row["amount"] for row in medicine_rows)

        medicine_groups.append({
            "batch": batch,
            "medicine_entries": medicine_rows,
            "total_medicine": total_medicine,
        })

    return render(request, "api/medicine_list.html", {
        "medicine_groups": medicine_groups,
        "is_admin": is_admin,
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
        medicine_name = request.POST.get("medicine_name", "").strip()
        medicine_type = request.POST.get("medicine_type", "medicine")

        MedicineEntry.objects.create(
            batch=batch,
            entry_date=entry_date,
            medicine_name=medicine_name or "Not specified",
            medicine_type=medicine_type,
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
    investor_profile = getattr(request.user, "investor_profile", None)

    if not is_admin and not investor_profile:
        return redirect("dashboard")

    report_rows = []

    # Final report should show closed/sold batches only
    closed_batches = Batch.objects.filter(
        Q(is_active=False) | Q(status__in=["closed", "sold"])
    ).select_related("shed").distinct().order_by(
        "-end_date",
        "-start_date",
        "batch_number"
    )

    def money(value):
        return float(value or 0)

    def get_sale_total_amount(sale):
        sale_total = getattr(sale, "total_amount", None)

        if sale_total is not None:
            return money(sale_total)

        birds_sold = money(getattr(sale, "birds_sold", 0))
        average_weight = money(getattr(sale, "average_weight_kg", 0))
        rate = money(getattr(sale, "rate_per_kg", 0))

        return birds_sold * average_weight * rate

    def get_sale_locked_cogs(sale):
        """
        This checks common locked COGS field names.
        Your existing SaleRecord may have one of these fields.
        """
        possible_fields = [
            "locked_cogs",
            "cogs_at_sale",
            "total_cogs_at_sale",
            "sold_cogs",
            "sale_cogs",
            "total_cost_at_sale",
        ]

        for field_name in possible_fields:
            if hasattr(sale, field_name):
                value = getattr(sale, field_name)
                if value is not None:
                    return money(value)

        return 0

    def get_batch_investor_reports(batch):
        investor_reports = []

        allocations = InvestorAllocation.objects.filter(
            batch=batch
        ).select_related(
            "investor__user"
        ).order_by(
            "investor__user__username"
        )

        for allocation in allocations:
            investor_user = allocation.investor.user
            investor_name = investor_user.get_full_name().strip() or investor_user.username

            if batch.bird_count_initial > 0:
                investor_share_ratio = allocation.birds_owned / batch.bird_count_initial
            else:
                investor_share_ratio = 0

            investor_reports.append({
                "allocation_id": allocation.id,
                "name": investor_name,
                "birds_owned": allocation.birds_owned,
                "share_percentage": round(investor_share_ratio * 100, 2),
            })

        return investor_reports

    def build_owner_inputs(batch):
        allocations = list(
            InvestorAllocation.objects.filter(
                batch=batch
            ).select_related("investor__user")
        )

        allocated_investor_birds = sum(
            allocation.birds_owned for allocation in allocations
        )

        admin_start_birds = batch.bird_count_initial - allocated_investor_birds

        owners = []

        if admin_start_birds > 0:
            owners.append({
                "type": "admin",
                "allocation_id": None,
                "start_birds": admin_start_birds,
            })

        for allocation in allocations:
            owners.append({
                "type": "investor",
                "allocation_id": allocation.id,
                "start_birds": allocation.birds_owned,
            })

        return owners

    def allocate_whole_count(batch, total_count, owners):
        total_count = int(total_count or 0)

        if total_count <= 0 or batch.bird_count_initial <= 0 or not owners:
            return [0] * len(owners)

        exact_values = []

        for owner in owners:
            exact_value = total_count * owner["start_birds"] / batch.bird_count_initial
            exact_values.append(exact_value)

        allocated_values = [int(value) for value in exact_values]

        remaining_count = total_count - sum(allocated_values)

        allocation_order = sorted(
            range(len(owners)),
            key=lambda index: (
                exact_values[index] - allocated_values[index],
                0 if owners[index]["type"] == "admin" else 1
            ),
            reverse=True
        )

        for index in allocation_order[:remaining_count]:
            allocated_values[index] += 1

        return allocated_values

    def build_report_row(batch, report_title, share_ratio, starting_birds, mortality, sold):
        sale_records = SaleRecord.objects.filter(batch=batch)

        total_sold = SaleRecord.objects.filter(
            batch=batch
        ).aggregate(total=Sum("birds_sold"))["total"] or 0

        total_mortality = MortalityRecord.objects.filter(
            batch=batch
        ).aggregate(total=Sum("count"))["total"] or 0

        total_batch_current = (
            batch.bird_count_initial
            - total_mortality
            - total_sold
        )

        total_revenue = 0
        locked_cogs = 0

        for sale in sale_records:
            total_revenue += get_sale_total_amount(sale)
            locked_cogs += get_sale_locked_cogs(sale)

        chick_cost = ChickCostEntry.objects.filter(
            batch=batch
        ).aggregate(total=Sum("chick_cost"))["total"] or 0

        carriage_cost = ChickCostEntry.objects.filter(
            batch=batch
        ).aggregate(total=Sum("carriage_cost"))["total"] or 0

        feed_cost = FeedEntry.objects.filter(
            batch=batch
        ).aggregate(total=Sum("amount"))["total"] or 0

        medicine_cost = MedicineEntry.objects.filter(
            batch=batch
        ).aggregate(total=Sum("amount"))["total"] or 0

        expenses = Expense.objects.filter(batch=batch)

        electricity_cost = expenses.filter(
            category="electricity"
        ).aggregate(total=Sum("amount"))["total"] or 0

        other_expenses = expenses.exclude(category="electricity")

        total_expenses = other_expenses.aggregate(
            total=Sum("amount")
        )["total"] or 0

        total_batch_cogs_current = (
            money(chick_cost)
            + money(carriage_cost)
            + money(feed_cost)
            + money(medicine_cost)
            + money(electricity_cost)
        )

        # Fallback only for old sales where locked COGS was not saved
        if locked_cogs == 0 and total_sold > 0 and batch.bird_count_initial > 0:
            locked_cogs = total_batch_cogs_current * (total_sold / batch.bird_count_initial)

        current_birds = starting_birds - mortality - sold

        share_revenue = round(total_revenue * share_ratio, 2)
        share_locked_cogs = round(locked_cogs * share_ratio, 2)
        share_expenses = round(money(total_expenses) * share_ratio, 2)

        net_income = round(
            share_revenue - share_locked_cogs - share_expenses,
            2
        )

        investment_base = share_locked_cogs + share_expenses

        # =====================================================
        # ADMIN REALIZED ROI — SOLD BIRDS ONLY
        # =====================================================

        roi = 0

        if investment_base > 0:
            roi = round(
                (net_income / investment_base) * 100,
                2
            )

        # =====================================================
        # TOTAL BATCH REALIZED ROI — SOLD BIRDS ONLY
        # =====================================================

        # Profit from birds already sold
        total_batch_net_income = round(
            total_revenue - locked_cogs,
            2
        )

        # Investment/cost of birds already sold
        total_batch_investment_base = locked_cogs

        total_batch_roi = 0

        if total_batch_investment_base > 0:
            total_batch_roi = round(
                (
                        total_batch_net_income
                        / total_batch_investment_base
                ) * 100,
                2
            )
        investor_reports = []
        if is_admin:
            investor_reports = get_batch_investor_reports(batch)

        return {
            "batch": batch,
            "report_title": report_title,
            "share_percentage": round(share_ratio * 100, 1),

            "starting_birds": starting_birds,
            "current_birds": current_birds,
            "mortality": mortality,
            "sold": sold,

            "revenue": share_revenue,
            "locked_cogs": share_locked_cogs,
            "expenses": share_expenses,
            "net_income": net_income,
            "roi": roi,

            "investor_reports": investor_reports,

            "total_batch_start": batch.bird_count_initial,
            "total_batch_current": total_batch_current,
            "total_batch_mortality": total_mortality,
            "total_batch_sold": total_sold,
            "total_batch_revenue": round(total_revenue, 2),
            "total_batch_cogs": round(locked_cogs, 2),
            "total_batch_expenses": round(money(total_expenses), 2),
            "total_batch_net_income": total_batch_net_income,
            "total_batch_roi": total_batch_roi,
        }

    for batch in closed_batches:
        total_mortality = MortalityRecord.objects.filter(
            batch=batch
        ).aggregate(total=Sum("count"))["total"] or 0

        total_sold = SaleRecord.objects.filter(
            batch=batch
        ).aggregate(total=Sum("birds_sold"))["total"] or 0

        owners = build_owner_inputs(batch)
        mortality_allocations = allocate_whole_count(batch, total_mortality, owners)
        sold_allocations = allocate_whole_count(batch, total_sold, owners)

        if is_admin:
            admin_index = None

            for index, owner in enumerate(owners):
                if owner["type"] == "admin":
                    admin_index = index
                    break

            if admin_index is None:
                continue

            admin_starting_birds = owners[admin_index]["start_birds"]
            admin_share_ratio = (
                admin_starting_birds / batch.bird_count_initial
                if batch.bird_count_initial > 0
                else 0
            )

            row = build_report_row(
                batch=batch,
                report_title="Admin Share Report",
                share_ratio=admin_share_ratio,
                starting_birds=admin_starting_birds,
                mortality=mortality_allocations[admin_index],
                sold=sold_allocations[admin_index],
            )

            report_rows.append(row)

        else:
            allocation = InvestorAllocation.objects.filter(
                batch=batch,
                investor=investor_profile
            ).first()

            if not allocation:
                continue

            investor_index = None

            for index, owner in enumerate(owners):
                if owner["type"] == "investor" and owner["allocation_id"] == allocation.id:
                    investor_index = index
                    break

            if investor_index is None:
                continue

            investor_share_ratio = (
                allocation.birds_owned / batch.bird_count_initial
                if batch.bird_count_initial > 0
                else 0
            )

            investor_user = allocation.investor.user
            investor_name = investor_user.get_full_name().strip() or investor_user.username

            row = build_report_row(
                batch=batch,
                report_title=f"{investor_name} Share Report",
                share_ratio=investor_share_ratio,
                starting_birds=allocation.birds_owned,
                mortality=mortality_allocations[investor_index],
                sold=sold_allocations[investor_index],
            )

            report_rows.append(row)

    return render(request, "api/batch_report.html", {
        "report_rows": report_rows,
        "is_admin": is_admin,
    })

def calculate_batch_report_data(batch, share_ratio=1):
    total_mortality = MortalityRecord.objects.filter(
        batch=batch
    ).aggregate(total=Sum("count"))["total"] or 0

    total_sold = SaleRecord.objects.filter(
        batch=batch
    ).aggregate(total=Sum("birds_sold"))["total"] or 0

    current_birds = batch.bird_count_initial - total_mortality - total_sold

    total_revenue = 0
    sale_records = SaleRecord.objects.filter(batch=batch)

    for sale in sale_records:
        total_revenue += float(sale.total_amount)

    chick_cost = ChickCostEntry.objects.filter(
        batch=batch
    ).aggregate(total=Sum("chick_cost"))["total"] or 0

    carriage_cost = ChickCostEntry.objects.filter(
        batch=batch
    ).aggregate(total=Sum("carriage_cost"))["total"] or 0

    feed_cost = FeedEntry.objects.filter(
        batch=batch
    ).aggregate(total=Sum("amount"))["total"] or 0

    medicine_cost = MedicineEntry.objects.filter(
        batch=batch
    ).aggregate(total=Sum("amount"))["total"] or 0

    expenses = Expense.objects.filter(batch=batch)

    electricity_cost = expenses.filter(
        category="electricity"
    ).aggregate(total=Sum("amount"))["total"] or 0

    total_expenses = expenses.exclude(
        category="electricity"
    ).aggregate(total=Sum("amount"))["total"] or 0

    total_cogs = (
        float(chick_cost)
        + float(carriage_cost)
        + float(feed_cost)
        + float(medicine_cost)
        + float(electricity_cost)
    )

    share_start_birds = round(batch.bird_count_initial * share_ratio)
    share_mortality = round(total_mortality * share_ratio)
    share_sold = round(total_sold * share_ratio)
    share_current_birds = share_start_birds - share_mortality - share_sold

    share_revenue = round(total_revenue * share_ratio, 2)
    share_total_cogs = round(total_cogs * share_ratio, 2)
    share_expenses = round(float(total_expenses) * share_ratio, 2)

    net_income = round(share_revenue - share_total_cogs - share_expenses, 2)

    roi = 0
    if share_total_cogs > 0:
        roi = round((net_income / share_total_cogs) * 100, 2)

    return {
        "start_birds": share_start_birds,
        "current_birds": share_current_birds,
        "mortality": share_mortality,
        "sold": share_sold,

        "revenue": share_revenue,
        "chick_cost": round(float(chick_cost) * share_ratio, 2),
        "feed_cost": round(float(feed_cost) * share_ratio, 2),
        "medicine_cost": round(float(medicine_cost) * share_ratio, 2),
        "electricity_cost": round(float(electricity_cost) * share_ratio, 2),
        "carriage_cost": round(float(carriage_cost) * share_ratio, 2),
        "total_cogs": share_total_cogs,
        "expenses": share_expenses,
        "net_income": net_income,
        "roi": roi,
    }

def get_static_logo_path():
    logo_path = finders.find("images/raynoor-logo.png")

    if logo_path:
        return logo_path

    static_root = getattr(settings, "STATIC_ROOT", None)

    if static_root:
        collected_logo_path = Path(static_root) / "images" / "raynoor-logo.png"

        if collected_logo_path.exists():
            return str(collected_logo_path)

    return None

@login_required
def batch_report_pdf_investor(request, batch_id, allocation_id):
    is_admin = request.user.is_superuser or request.user.is_staff

    if not is_admin:
        return redirect("dashboard")

    batch = get_object_or_404(Batch, id=batch_id)

    allocation = get_object_or_404(
        InvestorAllocation.objects.select_related("investor__user", "batch"),
        id=allocation_id,
        batch=batch
    )

    investor_user = allocation.investor.user
    investor_name = investor_user.get_full_name().strip() or investor_user.username

    if batch.bird_count_initial > 0:
        share_ratio = allocation.birds_owned / batch.bird_count_initial
    else:
        share_ratio = 0

    report = calculate_batch_report_data(batch, share_ratio)

    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=35,
        leftMargin=35,
        topMargin=35,
        bottomMargin=35,
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "TitleStyle",
        parent=styles["Title"],
        fontSize=20,
        textColor=colors.HexColor("#0b3f74"),
        spaceAfter=12,
    )

    heading_style = ParagraphStyle(
        "HeadingStyle",
        parent=styles["Heading2"],
        fontSize=14,
        textColor=colors.HexColor("#0b3f74"),
        spaceBefore=12,
        spaceAfter=8,
    )

    normal_style = styles["Normal"]

    elements = []

    logo_path = get_static_logo_path()

    if logo_path:
        logo_reader = ImageReader(logo_path)
        logo_width_px, logo_height_px = logo_reader.getSize()

        logo_width = 1.25 * inch
        logo_height = logo_width * (logo_height_px / logo_width_px)

        logo = Image(logo_path, width=logo_width, height=logo_height)
        logo.hAlign = "CENTER"

        elements.append(logo)
        elements.append(Spacer(1, 8))

    elements.append(Paragraph("RayNoor Farms", title_style))

    elements.append(Paragraph("Investor Batch Report", title_style))
    elements.append(Paragraph(f"<b>Investor:</b> {investor_name}", normal_style))
    elements.append(Paragraph(
        f"<b>Batch:</b> {batch.batch_type.title()} Batch #{batch.batch_number}",
        normal_style
    ))
    elements.append(Paragraph(f"<b>Shed:</b> {batch.shed.name}", normal_style))
    elements.append(Paragraph(f"<b>Share:</b> {round(share_ratio * 100, 2)}%", normal_style))
    elements.append(Paragraph(f"<b>Status:</b> {batch.status.title()}", normal_style))
    elements.append(Spacer(1, 12))

    elements.append(Paragraph("Bird Position", heading_style))

    bird_table = Table([
        ["Starting Birds", "Current Birds", "Mortality", "Sold"],
        [
            report["start_birds"],
            report["current_birds"],
            report["mortality"],
            report["sold"],
        ],
    ], colWidths=[1.55 * inch, 1.55 * inch, 1.55 * inch, 1.55 * inch])

    bird_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef4fb")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0b3f74")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d9e2ec")),
        ("PADDING", (0, 0), (-1, -1), 9),
    ]))

    elements.append(bird_table)
    elements.append(Spacer(1, 12))

    elements.append(Paragraph("Financial Summary", heading_style))

    finance_table = Table([
        ["Description", "Amount"],
        ["Revenue Share", f"Rs {report['revenue']:,.0f}"],
        ["Chick Cost Share", f"Rs {report['chick_cost']:,.0f}"],
        ["Feed Cost Share", f"Rs {report['feed_cost']:,.0f}"],
        ["Medicine Cost Share", f"Rs {report['medicine_cost']:,.0f}"],
        ["Electricity Cost Share", f"Rs {report['electricity_cost']:,.0f}"],
        ["Carriage Cost Share", f"Rs {report['carriage_cost']:,.0f}"],
        ["Total COGS Share", f"Rs {report['total_cogs']:,.0f}"],
        ["Expense Share", f"Rs {report['expenses']:,.0f}"],
        ["Net Income", f"Rs {report['net_income']:,.0f}"],
        ["ROI", f"{report['roi']:,.2f}%"],
    ], colWidths=[3.4 * inch, 2.6 * inch])

    finance_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0b3f74")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 1), (1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d9e2ec")),
        ("PADDING", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#f8fbff")),
    ]))

    elements.append(finance_table)
    elements.append(Spacer(1, 14))

    elements.append(Paragraph(
        "This report is generated from farm batch records including sales, mortality, feed, medicine, electricity, carriage, and other expenses.",
        normal_style
    ))

    def add_pdf_info(canvas, doc):
        canvas.setTitle(f"Investor Batch Report - {investor_name}")
        canvas.setAuthor("RayNoor Organic Farms")
        canvas.setSubject(f"{batch.batch_type.title()} Batch #{batch.batch_number}")

    doc.build(
        elements,
        onFirstPage=add_pdf_info,
        onLaterPages=add_pdf_info
    )

    pdf = buffer.getvalue()
    buffer.close()

    safe_investor_name = investor_name.replace(" ", "_")

    filename = (
        f"investor_report_{safe_investor_name}_batch_{batch.batch_number}.pdf"
    )

    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    response.write(pdf)

    return response

    return response