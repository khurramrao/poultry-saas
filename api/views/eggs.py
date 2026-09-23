from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from api.models.eggs import EggProductionEntry, EggSale, LayerHenCountHistory
from api.models.investors import InvestorAllocation
from api.models.sensor import Batch


MONEY_ZERO = Decimal("0.00")
MONEY_UNIT = Decimal("0.01")


def _money(value):
    if value in (None, ""):
        value = 0
    return Decimal(value).quantize(MONEY_UNIT)


def _is_admin(user):
    return user.is_superuser or user.is_staff



def _percent(numerator, denominator):
    if not denominator:
        return None
    return (
        Decimal(numerator) / Decimal(denominator) * Decimal("100")
    ).quantize(Decimal("0.1"))


def _hen_history_for_batch(batch):
    return list(
        LayerHenCountHistory.objects.filter(batch=batch).order_by(
            "effective_date",
            "id",
        )
    )


def _hen_count_from_history(history, target_date):
    count = None
    for record in history:
        if record.effective_date <= target_date:
            count = int(record.active_hens or 0)
        else:
            break
    return count or None


def _active_hens_on_date(batch, target_date):
    record = (
        LayerHenCountHistory.objects.filter(
            batch=batch,
            effective_date__lte=target_date,
        )
        .order_by("-effective_date", "-id")
        .first()
    )
    return int(record.active_hens) if record else None


def _daily_production_rows(entries, hen_history):
    daily = {}
    for entry in entries:
        row = daily.setdefault(
            entry.production_date,
            {
                "production_date": entry.production_date,
                "eggs_collected": 0,
                "damaged_eggs": 0,
                "usable_eggs": 0,
                "notes": [],
            },
        )
        row["eggs_collected"] += int(entry.eggs_collected or 0)
        row["damaged_eggs"] += int(entry.damaged_eggs or 0)
        row["usable_eggs"] += int(entry.usable_eggs or 0)
        if entry.notes and entry.notes.strip():
            row["notes"].append(entry.notes.strip())

    rows = []
    for production_date in sorted(daily.keys(), reverse=True):
        row = daily[production_date]
        active_hens = _hen_count_from_history(hen_history, production_date)
        row["active_hens"] = active_hens
        row["production_percent"] = _percent(
            row["eggs_collected"],
            active_hens,
        )
        row["notes_text"] = " · ".join(dict.fromkeys(row["notes"]))
        rows.append(row)
    return rows


def _monthly_performance(production_days, sales, display_ratio):
    monthly = defaultdict(
        lambda: {
            "collected": 0,
            "damaged": 0,
            "usable": 0,
            "days_recorded": 0,
            "days_with_hens": 0,
            "hen_days": 0,
            "net_sales": MONEY_ZERO,
        }
    )

    for day in production_days:
        month = day["production_date"].replace(day=1)
        row = monthly[month]
        row["collected"] += day["eggs_collected"]
        row["damaged"] += day["damaged_eggs"]
        row["usable"] += day["usable_eggs"]
        row["days_recorded"] += 1
        if day["active_hens"]:
            row["days_with_hens"] += 1
            row["hen_days"] += int(day["active_hens"])

    for sale in sales:
        month = sale.sale_date.replace(day=1)
        monthly[month]["net_sales"] += (
            _money(sale.total_amount) * display_ratio
        ).quantize(MONEY_UNIT)

    rows = []
    for month in sorted(monthly.keys(), reverse=True):
        row = monthly[month]
        complete_hen_coverage = (
            row["days_recorded"] > 0
            and row["days_with_hens"] == row["days_recorded"]
        )
        production_percent = None
        if complete_hen_coverage and row["hen_days"]:
            production_percent = _percent(row["collected"], row["hen_days"])

        avg_hens = None
        if complete_hen_coverage and row["days_recorded"]:
            avg_hens = (
                Decimal(row["hen_days"]) / Decimal(row["days_recorded"])
            ).quantize(Decimal("0.1"))

        rows.append({
            "month": month,
            "collected": row["collected"],
            "damaged": row["damaged"],
            "usable": row["usable"],
            "days_recorded": row["days_recorded"],
            "days_with_hens": row["days_with_hens"],
            "hen_days": row["hen_days"],
            "avg_hens": avg_hens,
            "production_percent": production_percent,
            "net_sales": _money(row["net_sales"]),
            "missing_hen_days": row["days_recorded"] - row["days_with_hens"],
        })

    return rows


def _available_layer_batches(user, include_closed=True):
    """
    Layer eligibility is based on the physical shed type.

    This intentionally uses shed__shed_type='layer' instead of batch_type,
    because the current project database contains an active batch in the
    Layer shed whose batch_type is still marked 'meat'.
    """
    qs = Batch.objects.filter(
        shed__shed_type="layer",
    ).select_related("shed")

    if not include_closed:
        qs = qs.filter(
            is_active=True,
            status="active",
        )

    if _is_admin(user):
        return qs.order_by("-is_active", "-start_date", "-id")

    if not hasattr(user, "investor_profile"):
        return Batch.objects.none()

    allocated_ids = InvestorAllocation.objects.filter(
        investor=user.investor_profile,
    ).values_list("batch_id", flat=True)

    return qs.filter(id__in=allocated_ids).order_by(
        "-is_active",
        "-start_date",
        "-id",
    )


def _batch_egg_totals(batch):
    production = EggProductionEntry.objects.filter(batch=batch).aggregate(
        collected=Sum("eggs_collected"),
        damaged=Sum("damaged_eggs"),
    )

    collected = int(production["collected"] or 0)
    damaged = int(production["damaged"] or 0)
    usable = max(collected - damaged, 0)

    sales = list(
        EggSale.objects.filter(batch=batch).order_by("-sale_date", "-id")
    )

    sold = sum(int(sale.eggs_sold or 0) for sale in sales)
    stock = max(usable - sold, 0)

    gross_sales = sum(
        (_money(sale.gross_amount) for sale in sales),
        MONEY_ZERO,
    )
    discount = sum(
        (_money(sale.discount_amount) for sale in sales),
        MONEY_ZERO,
    )
    net_sales = sum(
        (_money(sale.total_amount) for sale in sales),
        MONEY_ZERO,
    )

    return {
        "collected": collected,
        "damaged": damaged,
        "usable": usable,
        "sold": sold,
        "stock": stock,
        "gross_sales": _money(gross_sales),
        "discount": _money(discount),
        "net_sales": _money(net_sales),
        "sales": sales,
    }


@login_required
def egg_dashboard(request):
    is_admin = _is_admin(request.user)

    if not is_admin and not hasattr(request.user, "investor_profile"):
        messages.error(request, "You are not allowed to view egg records.")
        return redirect("dashboard")

    batches = list(_available_layer_batches(request.user, include_closed=False))
    today = timezone.localdate()
    current_month = today.replace(day=1)

    overview = {
        "collected": 0,
        "damaged": 0,
        "usable": 0,
        "sold": 0,
        "stock": 0,
        "net_sales": MONEY_ZERO,
        "active_hens": 0,
        "month_collected": 0,
        "month_hen_days": 0,
        "month_missing_hen_days": 0,
        "month_production_percent": None,
    }

    batch_rows = []

    for batch in batches:
        totals = _batch_egg_totals(batch)
        all_production_entries = list(
            EggProductionEntry.objects.filter(batch=batch).order_by(
                "-production_date",
                "-id",
            )
        )
        hen_history = _hen_history_for_batch(batch)
        production_days = _daily_production_rows(
            all_production_entries,
            hen_history,
        )
        current_active_hens = _hen_count_from_history(hen_history, today)

        ownership_percent = None
        egg_sales_share = None
        recent_sales = []
        ratio = Decimal("1")

        if not is_admin:
            allocation = InvestorAllocation.objects.filter(
                batch=batch,
                investor=request.user.investor_profile,
            ).first()

            ratio = Decimal("0")
            if allocation and batch.bird_count_initial:
                ratio = (
                    Decimal(allocation.birds_owned)
                    / Decimal(batch.bird_count_initial)
                )

            ownership_percent = (ratio * Decimal("100")).quantize(
                Decimal("0.1")
            )
            egg_sales_share = (
                totals["net_sales"] * ratio
            ).quantize(MONEY_UNIT)

        display_ratio = Decimal("1") if is_admin else ratio
        for sale in totals["sales"][:12]:
            recent_sales.append({
                "sale_date": sale.sale_date,
                "buyer_name": sale.buyer_name,
                "eggs_sold": sale.eggs_sold,
                "rate_per_egg": sale.rate_per_egg,
                "discount_amount": (
                    _money(sale.discount_amount) * display_ratio
                ).quantize(MONEY_UNIT),
                "display_amount": (
                    _money(sale.total_amount) * display_ratio
                ).quantize(MONEY_UNIT),
            })

        monthly_rows = _monthly_performance(
            production_days,
            totals["sales"],
            display_ratio,
        )
        current_month_row = next(
            (row for row in monthly_rows if row["month"] == current_month),
            None,
        )

        batch_rows.append({
            "batch": batch,
            "totals": totals,
            "production_days": production_days[:12],
            "recent_sales": recent_sales,
            "ownership_percent": ownership_percent,
            "egg_sales_share": egg_sales_share,
            "current_active_hens": current_active_hens,
            "hen_history": list(reversed(hen_history[-6:])),
            "monthly_rows": monthly_rows[:12],
            "current_month": current_month_row,
        })

        overview["collected"] += totals["collected"]
        overview["damaged"] += totals["damaged"]
        overview["usable"] += totals["usable"]
        overview["sold"] += totals["sold"]
        overview["stock"] += totals["stock"]
        overview["active_hens"] += current_active_hens or 0

        if current_month_row:
            overview["month_collected"] += current_month_row["collected"]
            overview["month_hen_days"] += current_month_row["hen_days"]
            overview["month_missing_hen_days"] += current_month_row["missing_hen_days"]

        if is_admin:
            overview["net_sales"] += totals["net_sales"]
        else:
            overview["net_sales"] += egg_sales_share or MONEY_ZERO

    overview["net_sales"] = _money(overview["net_sales"])
    if (
        overview["month_hen_days"]
        and overview["month_missing_hen_days"] == 0
    ):
        overview["month_production_percent"] = _percent(
            overview["month_collected"],
            overview["month_hen_days"],
        )

    return render(
        request,
        "api/egg_dashboard.html",
        {
            "batch_rows": batch_rows,
            "overview": overview,
            "is_admin": is_admin,
            "current_month": current_month,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def active_hens_history(request):
    if not _is_admin(request.user):
        messages.error(request, "Only admin can update active laying hens.")
        return redirect("egg_dashboard")

    batches = list(_available_layer_batches(request.user, include_closed=False))
    today = timezone.localdate()

    if request.method == "POST":
        batch = get_object_or_404(
            Batch.objects.select_related("shed"),
            pk=request.POST.get("batch_id"),
        )
        if (
            batch.shed.shed_type != "layer"
            or not batch.is_active
            or batch.status != "active"
        ):
            messages.error(request, "Active hens can only be updated for an active Layer batch.")
            return redirect("active_hens_history")

        try:
            effective_date = date.fromisoformat(
                request.POST.get("effective_date") or str(today)
            )
            active_hens = int(request.POST.get("active_hens") or 0)
        except (TypeError, ValueError):
            messages.error(request, "Enter a valid effective date and active hen count.")
            return redirect("active_hens_history")

        entry = LayerHenCountHistory(
            batch=batch,
            effective_date=effective_date,
            active_hens=active_hens,
            notes=(request.POST.get("notes") or "").strip(),
            recorded_by=request.user,
        )
        try:
            entry.full_clean(exclude=["id"])
        except ValidationError as error:
            messages.error(request, "; ".join(error.messages))
            return redirect("active_hens_history")

        existing = LayerHenCountHistory.objects.filter(
            batch=batch,
            effective_date=effective_date,
        ).first()
        if existing:
            existing.active_hens = active_hens
            existing.notes = entry.notes
            existing.recorded_by = request.user
            try:
                existing.full_clean()
                existing.save()
            except ValidationError as error:
                messages.error(request, "; ".join(error.messages))
                return redirect("active_hens_history")
            action = "updated"
        else:
            entry.save()
            action = "recorded"

        messages.success(
            request,
            f"Active hens {action}: {active_hens} hens from {effective_date:%d %b %Y}.",
        )
        return redirect("active_hens_history")

    history_rows = list(
        LayerHenCountHistory.objects.filter(batch__in=batches)
        .select_related("batch", "batch__shed", "recorded_by")
        .order_by("-effective_date", "-id")
    )
    for batch in batches:
        batch.current_active_hens = _active_hens_on_date(batch, today)

    return render(
        request,
        "api/active_hens_history.html",
        {
            "batches": batches,
            "history_rows": history_rows,
            "today": today,
            "is_admin": True,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def add_egg_production(request):
    if not _is_admin(request.user):
        messages.error(request, "Only admin can add egg production entries.")
        return redirect("egg_dashboard")

    batches = list(_available_layer_batches(request.user, include_closed=False))
    today = timezone.localdate()
    for batch in batches:
        batch.current_active_hens = _active_hens_on_date(batch, today)

    if request.method == "POST":
        batch = get_object_or_404(
            Batch.objects.select_related("shed"),
            pk=request.POST.get("batch_id"),
        )

        if (
            batch.shed.shed_type != "layer"
            or not batch.is_active
            or batch.status != "active"
        ):
            messages.error(
                request,
                "Egg production can only be added to an active Layer shed batch.",
            )
            return redirect("add_egg_production")

        try:
            production_date = date.fromisoformat(
                request.POST.get("production_date") or str(today)
            )
            eggs_collected = int(request.POST.get("eggs_collected") or 0)
            damaged_eggs = int(request.POST.get("damaged_eggs") or 0)
        except (TypeError, ValueError):
            messages.error(request, "Enter a valid date and whole-number egg quantities.")
            return redirect("add_egg_production")

        if production_date > today:
            messages.error(request, "Production date cannot be in the future.")
            return redirect("add_egg_production")

        if eggs_collected < 0 or damaged_eggs < 0:
            messages.error(request, "Egg quantities cannot be negative.")
            return redirect("add_egg_production")

        if damaged_eggs > eggs_collected:
            messages.error(
                request,
                "Damaged eggs cannot exceed total eggs collected.",
            )
            return redirect("add_egg_production")

        entry = EggProductionEntry(
            batch=batch,
            production_date=production_date,
            eggs_collected=eggs_collected,
            damaged_eggs=damaged_eggs,
            notes=(request.POST.get("notes") or "").strip(),
            recorded_by=request.user,
        )

        try:
            entry.full_clean()
            entry.save()
        except ValidationError as error:
            messages.error(request, "; ".join(error.messages))
            return redirect("add_egg_production")

        active_hens = _active_hens_on_date(batch, production_date)
        production_percent = _percent(eggs_collected, active_hens)
        if production_percent is None:
            messages.warning(
                request,
                "Egg production saved, but production % is unavailable until Active Hens is set for this date.",
            )
        else:
            messages.success(
                request,
                f"Egg production recorded: {entry.usable_eggs} usable eggs · {production_percent}% production from {active_hens} active hens.",
            )
        return redirect("egg_dashboard")

    return render(
        request,
        "api/add_egg_production.html",
        {"batches": batches},
    )


@login_required
@require_http_methods(["GET", "POST"])
def add_egg_sale(request):
    if not _is_admin(request.user):
        messages.error(request, "Only admin can record egg sales.")
        return redirect("egg_dashboard")

    batches = list(_available_layer_batches(request.user, include_closed=False))

    batch_options = []
    for batch in batches:
        totals = _batch_egg_totals(batch)
        batch.current_egg_stock = totals["stock"]
        batch_options.append(batch)

    if request.method == "POST":
        try:
            with transaction.atomic():
                batch = get_object_or_404(
                    Batch.objects.select_for_update().select_related("shed"),
                    pk=request.POST.get("batch_id"),
                )

                if (
                    batch.shed.shed_type != "layer"
                    or not batch.is_active
                    or batch.status != "active"
                ):
                    raise ValidationError(
                        "Egg sales can only be recorded for an active Layer shed batch."
                    )

                try:
                    eggs_sold = int(request.POST.get("eggs_sold") or 0)
                    rate_per_egg = Decimal(
                        str(request.POST.get("rate_per_egg") or "0")
                    ).quantize(MONEY_UNIT)
                    discount_amount = Decimal(
                        str(request.POST.get("discount_amount") or "0")
                    ).quantize(MONEY_UNIT)
                except (TypeError, ValueError, InvalidOperation):
                    raise ValidationError("Enter valid sale quantity, rate, and discount.")

                if eggs_sold <= 0:
                    raise ValidationError("Egg quantity sold must be greater than zero.")

                if rate_per_egg <= 0:
                    raise ValidationError("Rate per egg must be greater than zero.")

                if discount_amount < 0:
                    raise ValidationError("Discount cannot be negative.")

                current_stock = _batch_egg_totals(batch)["stock"]

                if eggs_sold > current_stock:
                    raise ValidationError(
                        f"Only {current_stock} eggs are currently available in stock."
                    )

                gross_amount = (
                    Decimal(eggs_sold) * rate_per_egg
                ).quantize(MONEY_UNIT)

                if discount_amount > gross_amount:
                    raise ValidationError(
                        "Discount cannot exceed the gross egg sale amount."
                    )

                sale = EggSale(
                    batch=batch,
                    sale_date=(
                        request.POST.get("sale_date")
                        or timezone.localdate()
                    ),
                    buyer_name=(request.POST.get("buyer_name") or "").strip(),
                    eggs_sold=eggs_sold,
                    rate_per_egg=rate_per_egg,
                    discount_amount=discount_amount,
                    payment_method=(request.POST.get("payment_method") or "cash"),
                    notes=(request.POST.get("notes") or "").strip(),
                    recorded_by=request.user,
                )

                sale.full_clean()
                sale.save()

        except ValidationError as error:
            messages.error(request, "; ".join(error.messages))
            return redirect("add_egg_sale")

        messages.success(
            request,
            f"Egg sale recorded successfully. Net sale: Rs {sale.total_amount:,.2f}",
        )
        return redirect("egg_dashboard")

    return render(
        request,
        "api/add_egg_sale.html",
        {"batches": batch_options},
    )
