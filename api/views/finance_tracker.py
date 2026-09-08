from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.contrib import messages
from django.views.decorators.http import require_http_methods


from decimal import Decimal
import math
import re

from api.models.sensor import Batch, MortalityRecord
from api.models.sales import ChickCostEntry, SaleRecord, Expense
from api.models.investors import InvestorAllocation, FeedEntry, MedicineEntry
from api.models.eggs import EggProductionEntry, EggSale

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

from api.services.finance_reconciliation import build_finance_data, build_report_position
from django.contrib.staticfiles import finders
from reportlab.platypus import Image





@login_required
@login_required
def finance_tracker(request):
    if not (request.user.is_superuser or request.user.is_staff or hasattr(request.user, "investor_profile")):
        return redirect("dashboard")
    context = build_finance_data(request.user, request.GET.get("status", "all"))
    return render(request, "api/finance_tracker.html", context)


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

        all_expense_cost = Expense.objects.filter(
            batch=batch
        ).aggregate(
            total=Sum("amount")
        )["total"] or Decimal("0")

        total_cogs = (
                chick_cost
                + carriage_cost
                + feed_cost
                + medicine_cost
                + all_expense_cost
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
@login_required
def batch_report(request):
    is_admin = request.user.is_superuser or request.user.is_staff
    if not is_admin and not hasattr(request.user, "investor_profile"):
        return redirect("dashboard")
    data = build_finance_data(request.user, "closed")
    report_rows = []
    for row in data["finance_rows"]:
        # The complete batch is shown once. Admin may inspect every owner;
        # investors receive only their own owner row from the service.
        row["report_owner_rows"] = row["owner_rows"]
        row["report_position"] = build_report_position(
            row, None if is_admin else row["current_user_owner"]
        )
        report_rows.append(row)
    return render(request, "api/batch_report.html", {
        "report_rows": report_rows,
        "is_admin": is_admin,
    })


def calculate_batch_report_data(batch, share_ratio=1, allocation_id=None):
    """Compatibility adapter; all figures come from the shared engine."""
    from django.contrib.auth import get_user_model
    from django.core.exceptions import PermissionDenied
    admin = get_user_model().objects.filter(is_superuser=True).order_by("pk").first()
    if admin is None:
        raise PermissionDenied("An administrator account is required.")
    rows = build_finance_data(admin, "all", batch_ids=[batch.pk])["finance_rows"]
    if not rows:
        raise ValueError("Batch not found in the finance calculation.")
    row = rows[0]
    owner = None
    if allocation_id is not None:
        owner = next((o for o in row["all_owner_rows"] if o.get("allocation_id") == allocation_id), None)
        if owner is None:
            raise ValueError("The investor allocation does not belong to this batch.")
    elif Decimal(str(share_ratio)) != Decimal("1"):
        owner = next((o for o in row["all_owner_rows"] if o["share_ratio"] == Decimal(str(share_ratio))), None)
        if owner is None:
            raise ValueError("Use the investor allocation ID for an exact ownership report.")
    return build_report_position(row, owner)

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
@login_required
def batch_report_pdf_investor(request, batch_id, allocation_id):
    if not (request.user.is_superuser or request.user.is_staff):
        return redirect("dashboard")
    batch = get_object_or_404(Batch.objects.select_related("shed"), pk=batch_id)
    allocation = get_object_or_404(
        InvestorAllocation.objects.select_related("investor__user"),
        pk=allocation_id, batch=batch,
    )
    rows = build_finance_data(request.user, "all", batch_ids=[batch.pk])["finance_rows"]
    row = rows[0]
    owner = next((o for o in row["all_owner_rows"] if o.get("allocation_id") == allocation.pk), None)
    if owner is None:
        raise ValueError("Investor allocation could not be reconciled.")
    from xml.sax.saxutils import escape
    investor_name = allocation.investor.user.get_full_name().strip() or allocation.investor.user.username
    report = build_report_position(row, owner)
    def money_text(value):
        return "Rs {:,.2f}".format(Decimal(value or 0))
    def percent_text(value):
        return "{:.2f}%".format(Decimal(value or 0))
    def safe(value):
        return escape(str(value))
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=36, leftMargin=36,
                            topMargin=34, bottomMargin=34)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="RNTitle", parent=styles["Title"], fontSize=18,
                              leading=22, textColor=colors.HexColor("#123F6C"), spaceAfter=8))
    styles.add(ParagraphStyle(name="RNHeading", parent=styles["Heading2"], fontSize=11,
                              textColor=colors.HexColor("#123F6C"), spaceBefore=10, spaceAfter=6))
    normal = styles["Normal"]
    elements = [
        Paragraph("RayNoor Organic Farms", styles["RNTitle"]),
        Paragraph("Investor Batch Report", styles["RNHeading"]),
        Paragraph("<b>Investor:</b> " + safe(investor_name), normal),
        Paragraph("<b>Batch:</b> " + safe(row["batch"].shed.shed_type.title()) + " Batch #" + safe(batch.batch_number), normal),
        Paragraph("<b>Shed:</b> " + safe(batch.shed.name), normal),
        Paragraph("<b>Ownership:</b> " + percent_text(owner["percentage"]) + " | <b>Status:</b> " + safe(batch.get_status_display()), normal),
        Spacer(1, 10),
    ]
    def add_table(heading, data, widths=None):
        elements.append(Paragraph(heading, styles["RNHeading"]))
        table = Table(data, colWidths=widths or [3.5*inch, 3.0*inch], hAlign="LEFT", repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0,0),(-1,0),colors.HexColor("#123F6C")),
            ("TEXTCOLOR",(0,0),(-1,0),colors.white),
            ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
            ("BACKGROUND",(0,1),(-1,-1),colors.HexColor("#F7FAFE")),
            ("LINEBELOW",(0,0),(-1,-1),0.35,colors.HexColor("#DBE4EE")),
            ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
            ("LEFTPADDING",(0,0),(-1,-1),9), ("RIGHTPADDING",(0,0),(-1,-1),9),
            ("TOPPADDING",(0,0),(-1,-1),7), ("BOTTOMPADDING",(0,0),(-1,-1),7),
            ("ALIGN",(1,1),(-1,-1),"RIGHT"),
        ]))
        elements.append(table)
    add_table("Bird Position", [
        ["Measure", "Birds"],
        ["Starting Birds", str(report["start_birds"])],
        ["Mortality", str(report["mortality"])],
        ["Sold", str(report["sold"])],
        ["Current Birds", str(report["current_birds"])],
    ])
    add_table("Final Financial Summary" if row["is_final"] else "Financial Position to Date", [
        ["Description", "Owner Share"],
        ["Net Bird Sales", money_text(report["revenue"])],
        ["Egg Net Sales", money_text(report["egg_revenue"])],
        ["Total Revenue", money_text(report["total_revenue"])],
        ["Chick Cost", money_text(report["chick_cost"])],
        ["Carriage", money_text(report["carriage_cost"])],
        ["Feed", money_text(report["feed_cost"])],
        ["Medicine", money_text(report["medicine_cost"])],
        ["All Expenses", money_text(report["expenses"])],
        ["Total Recorded COGS", money_text(report["total_cogs"])],
        ["Final Profit / Loss" if row["is_final"] else "Profit / Loss to Date", money_text(report["net_income"])],
        ["Final ROI" if row["is_final"] else "ROI to Date", percent_text(report["roi"])],
    ])
    add_table("Historical Sale Cost Reconciliation", [
        ["Description", "Owner Share"],
        ["Sale-locked COGS", money_text(report["locked_cogs"])],
        ["Remaining Live Inventory Cost", money_text(report["remaining_live_inventory_cost"])],
        ["Unallocated Closing Costs", money_text(report["closing_cost_adjustment"])],
        ["Total Recorded COGS", money_text(report["total_cogs"])],
    ])
    elements.append(Spacer(1, 8))
    elements.append(Paragraph(
        "All recorded expenses are included in COGS. Final profit equals total bird and egg revenue less total recorded batch costs. Historical sale-locked costs are preserved. Monetary detail is shown to two decimals; no journal entries are changed by this report.", normal))
    for warning in row["reconciliation_warnings"]:
        elements.append(Spacer(1, 4))
        elements.append(Paragraph("<b>Review:</b> " + safe(warning), normal))
    def page_info(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#64748B"))
        canvas.drawRightString(A4[0]-36, 20, "Page " + str(document.page))
        canvas.restoreState()
    doc.build(elements, onFirstPage=page_info, onLaterPages=page_info)
    pdf = buffer.getvalue()
    buffer.close()
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", investor_name).strip("_") or "investor"
    filename = "investor_report_{}_batch_{}.pdf".format(safe_name, batch.pk)
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = 'inline; filename="{}"'.format(filename)
    return response
