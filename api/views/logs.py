from datetime import date
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from api.models.investors import (
    FeedEntry,
    InvestorAllocation,
    MedicineEntry,
    UserFeedStatus,
)
from api.models.sales import Expense, SaleRecord
from api.models.sensor import Batch, MortalityRecord


@login_required
@require_http_methods(["GET", "POST"])
def daily_log(request):
    is_admin = request.user.is_superuser or request.user.is_staff
    is_investor = hasattr(request.user, "investor_profile")

    if is_admin:
        accessible_batches = Batch.objects.filter(
            is_active=True
        ).order_by("-start_date", "batch_number")

    elif is_investor:
        investor_batch_ids = InvestorAllocation.objects.filter(
            investor=request.user.investor_profile
        ).values_list("batch_id", flat=True)

        accessible_batches = Batch.objects.filter(
            id__in=investor_batch_ids,
            is_active=True,
        ).order_by("-start_date", "batch_number")

    else:
        messages.error(request, "You are not allowed to view daily log.")
        return redirect("dashboard")

    if request.method == "POST":
        if not is_admin:
            messages.error(request, "Only admin can add daily log records.")
            return redirect("daily_log")

        batch_id = request.POST.get("batch_id")
        batch = get_object_or_404(Batch, id=batch_id)

        if batch.status == "closed" or not batch.is_active:
            messages.error(
                request,
                "This batch is closed. You cannot add mortality.",
            )
            return redirect("daily_log")

        log_date = request.POST.get("date")
        mortality_count = request.POST.get("mortality_count") or 0
        mortality_notes = request.POST.get("mortality_notes", "")

        MortalityRecord.objects.create(
            batch=batch,
            date=log_date,
            count=int(mortality_count),
            notes=mortality_notes,
        )

        messages.success(request, "Daily log saved successfully.")
        return redirect("daily_log")

    log_items = []

    mortality_records = MortalityRecord.objects.filter(
        batch__in=accessible_batches
    ).select_related("batch", "batch__shed").order_by("-date", "-id")

    sale_records = SaleRecord.objects.filter(
        batch__in=accessible_batches
    ).select_related("batch", "batch__shed").order_by("-sale_date", "-id")

    feed_records = FeedEntry.objects.filter(
        batch__in=accessible_batches
    ).select_related("batch", "batch__shed").order_by("-entry_date", "-id")

    medicine_records = MedicineEntry.objects.filter(
        batch__in=accessible_batches
    ).select_related("batch", "batch__shed").order_by("-entry_date", "-id")

    expense_records = Expense.objects.filter(
        batch__in=accessible_batches
    ).select_related("batch", "batch__shed").order_by("-expense_date", "-id")

    owner_cache = {}

    def build_owner_inputs_for_batch(batch):
        """Return the Admin residual ownership plus every investor allocation."""
        if batch.id in owner_cache:
            return owner_cache[batch.id]

        allocations = list(
            InvestorAllocation.objects.filter(
                batch=batch
            ).select_related("investor__user").order_by("id")
        )

        allocated_investor_birds = sum(
            allocation.birds_owned
            for allocation in allocations
        )

        owner_inputs = []
        admin_birds = batch.bird_count_initial - allocated_investor_birds

        if admin_birds > 0:
            owner_inputs.append({
                "name": "Admin",
                "birds": admin_birds,
                "is_admin_owner": True,
                "user_id": None,
            })

        for allocation in allocations:
            investor_user = allocation.investor.user
            investor_name = (
                investor_user.get_full_name().strip()
                or investor_user.username
            )

            owner_inputs.append({
                "name": investor_name,
                "birds": allocation.birds_owned,
                "is_admin_owner": False,
                "user_id": investor_user.id,
            })

        owner_cache[batch.id] = owner_inputs
        return owner_inputs

    def owner_percentage(batch, owner):
        if not batch.bird_count_initial:
            return 0.0

        return round(
            (owner["birds"] / batch.bird_count_initial) * 100,
            1,
        )

    def allocate_count_to_owners(batch, total_count, owner_inputs):
        """Allocate an integer count while guaranteeing the rows sum to total_count."""
        total_count = int(total_count or 0)

        if (
            total_count <= 0
            or batch.bird_count_initial <= 0
            or not owner_inputs
        ):
            return [0] * len(owner_inputs)

        exact_values = [
            total_count * owner["birds"] / batch.bird_count_initial
            for owner in owner_inputs
        ]
        allocated_values = [int(value) for value in exact_values]
        remaining_count = total_count - sum(allocated_values)

        allocation_order = sorted(
            range(len(owner_inputs)),
            key=lambda index: (
                exact_values[index] - allocated_values[index],
                0 if owner_inputs[index]["is_admin_owner"] else 1,
            ),
            reverse=True,
        )

        for index in allocation_order[:remaining_count]:
            allocated_values[index] += 1

        return allocated_values

    def allocate_decimal_to_owners(
        batch,
        total_value,
        owner_inputs,
        decimal_places=2,
    ):
        """Allocate money/weight by ownership and keep the rounded total exact."""
        total_value = Decimal(str(total_value or 0))
        quantum = Decimal("1").scaleb(-decimal_places)
        rounded_total = total_value.quantize(quantum, rounding=ROUND_HALF_UP)

        if (
            rounded_total == 0
            or batch.bird_count_initial <= 0
            or not owner_inputs
        ):
            return [Decimal("0").quantize(quantum)] * len(owner_inputs)

        exact_values = [
            rounded_total
            * Decimal(owner["birds"])
            / Decimal(batch.bird_count_initial)
            for owner in owner_inputs
        ]

        # Farm costs and sales are positive values. ROUND_DOWN lets us distribute
        # any final cents/grams by largest remainder so the displayed rows add up.
        allocated_values = [
            value.quantize(quantum, rounding=ROUND_DOWN)
            for value in exact_values
        ]

        remaining_units = int(
            ((rounded_total - sum(allocated_values)) / quantum)
            .to_integral_value(rounding=ROUND_HALF_UP)
        )

        allocation_order = sorted(
            range(len(owner_inputs)),
            key=lambda index: (
                exact_values[index] - allocated_values[index],
                0 if owner_inputs[index]["is_admin_owner"] else 1,
            ),
            reverse=True,
        )

        for index in allocation_order[:max(remaining_units, 0)]:
            allocated_values[index] += quantum

        return allocated_values

    def format_money(value):
        value = Decimal(str(value or 0)).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
        return f"Rs {value:,.0f}"

    def format_weight(value):
        value = Decimal(str(value or 0)).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        formatted = f"{value:,.2f}".rstrip("0").rstrip(".")
        return f"{formatted} kg"

    def make_metric(label, value, emphasis=False):
        return {
            "label": label,
            "value": value,
            "emphasis": emphasis,
        }

    def make_summary_stat(label, value, subtext=""):
        return {
            "label": label,
            "value": value,
            "subtext": subtext,
        }

    def make_owner_row(batch, owner, event_metrics):
        return {
            "name": owner["name"],
            "user_id": owner["user_id"],
            "is_admin_owner": owner["is_admin_owner"],
            "metrics": [
                make_metric(
                    "Share",
                    f"{owner_percentage(batch, owner):.1f}%",
                ),
                make_metric(
                    "Starting Birds",
                    f"{int(owner['birds']):,}",
                ),
                *event_metrics,
            ],
        }

    def visible_ownership_rows(rows):
        if is_admin:
            return rows

        if is_investor:
            return [
                row
                for row in rows
                if row["user_id"] == request.user.id
            ]

        return []

    def ownership_panel_title():
        return "Ownership Impact" if is_admin else "Your Ownership Impact"

    def get_mortality_impact_rows(record):
        batch = record.batch
        owner_inputs = build_owner_inputs_for_batch(batch)

        previous_mortality_before_date = sum(
            MortalityRecord.objects.filter(
                batch=batch,
                date__lt=record.date,
            ).values_list("count", flat=True)
        )

        previous_mortality_same_date = sum(
            MortalityRecord.objects.filter(
                batch=batch,
                date=record.date,
                id__lt=record.id,
            ).values_list("count", flat=True)
        )

        previous_total_mortality = (
            previous_mortality_before_date
            + previous_mortality_same_date
        )
        after_total_mortality = (
            previous_total_mortality
            + int(record.count or 0)
        )

        before_allocations = allocate_count_to_owners(
            batch,
            previous_total_mortality,
            owner_inputs,
        )
        after_allocations = allocate_count_to_owners(
            batch,
            after_total_mortality,
            owner_inputs,
        )

        rows = []

        for index, owner in enumerate(owner_inputs):
            mortality_impact = (
                after_allocations[index]
                - before_allocations[index]
            )

            rows.append(
                make_owner_row(
                    batch,
                    owner,
                    [
                        make_metric(
                            "Mortality Impact",
                            f"{mortality_impact:,}",
                            emphasis=True,
                        )
                    ],
                )
            )

        return rows

    def get_sale_impact_rows(sale):
        batch = sale.batch
        owner_inputs = build_owner_inputs_for_batch(batch)

        birds_allocations = allocate_count_to_owners(
            batch,
            sale.birds_sold,
            owner_inputs,
        )
        weight_allocations = allocate_decimal_to_owners(
            batch,
            sale.total_weight_kg,
            owner_inputs,
            decimal_places=2,
        )
        revenue_allocations = allocate_decimal_to_owners(
            batch,
            sale.total_amount,
            owner_inputs,
            decimal_places=0,
        )

        rows = []

        for index, owner in enumerate(owner_inputs):
            rows.append(
                make_owner_row(
                    batch,
                    owner,
                    [
                        make_metric(
                            "Birds Sold",
                            f"{birds_allocations[index]:,}",
                        ),
                        make_metric(
                            "Weight",
                            format_weight(weight_allocations[index]),
                        ),
                        make_metric(
                            "Revenue Share",
                            format_money(revenue_allocations[index]),
                            emphasis=True,
                        ),
                    ],
                )
            )

        return rows

    def get_cost_impact_rows(batch, total_amount, impact_label):
        owner_inputs = build_owner_inputs_for_batch(batch)
        amount_allocations = allocate_decimal_to_owners(
            batch,
            total_amount,
            owner_inputs,
            decimal_places=0,
        )

        rows = []

        for index, owner in enumerate(owner_inputs):
            rows.append(
                make_owner_row(
                    batch,
                    owner,
                    [
                        make_metric(
                            impact_label,
                            format_money(amount_allocations[index]),
                            emphasis=True,
                        )
                    ],
                )
            )

        return rows

    def current_user_row(rows):
        if is_admin:
            for row in rows:
                if row["is_admin_owner"]:
                    return row
            return None

        for row in rows:
            if row["user_id"] == request.user.id:
                return row

        return None

    def metric_value(row, label, default=""):
        if not row:
            return default

        for metric in row["metrics"]:
            if metric["label"] == label:
                return metric["value"]

        return default

    for record in mortality_records:
        impact_rows = get_mortality_impact_rows(record)
        user_row = current_user_row(impact_rows)

        summary_stats = [
            make_summary_stat(
                "Total Mortality",
                f"{int(record.count or 0):,}",
            )
        ]

        if not is_admin:
            summary_stats.extend([
                make_summary_stat(
                    "Your Share",
                    metric_value(user_row, "Share", "0.0%"),
                ),
                make_summary_stat(
                    "Your Mortality Impact",
                    metric_value(user_row, "Mortality Impact", "0"),
                ),
            ])

        log_items.append({
            "type": "mortality",
            "icon": "⚰️",
            "title": "Mortality Recorded",
            "date": record.date,
            "batch": record.batch,
            "notes": record.notes,
            "summary_stats": summary_stats,
            "ownership_title": ownership_panel_title(),
            "ownership_breakdown": visible_ownership_rows(impact_rows),
        })

    for sale in sale_records:
        impact_rows = get_sale_impact_rows(sale)
        user_row = current_user_row(impact_rows)

        if is_admin:
            summary_stats = [
                make_summary_stat(
                    "Birds Sold",
                    f"{int(sale.birds_sold or 0):,}",
                ),
                make_summary_stat(
                    "Total Weight",
                    format_weight(sale.total_weight_kg),
                ),
                make_summary_stat(
                    "Net Sale Revenue",
                    format_money(sale.total_amount),
                ),
            ]
        else:
            summary_stats = [
                make_summary_stat(
                    "Your Birds Sold Share",
                    metric_value(user_row, "Birds Sold", "0"),
                ),
                make_summary_stat(
                    "Your Weight Share",
                    metric_value(user_row, "Weight", "0 kg"),
                ),
                make_summary_stat(
                    "Your Revenue Share",
                    metric_value(user_row, "Revenue Share", "Rs 0"),
                ),
            ]

        log_items.append({
            "type": "sale",
            "icon": "💰",
            "title": "Sale Recorded",
            "date": sale.sale_date,
            "batch": sale.batch,
            "notes": sale.notes,
            "summary_stats": summary_stats,
            "ownership_title": ownership_panel_title(),
            "ownership_breakdown": visible_ownership_rows(impact_rows),
        })

    for feed in feed_records:
        impact_rows = get_cost_impact_rows(
            feed.batch,
            feed.amount,
            "Feed Cost Share",
        )
        user_row = current_user_row(impact_rows)

        log_items.append({
            "type": "feed",
            "icon": "🌾",
            "title": "Feed Added",
            "date": feed.entry_date,
            "batch": feed.batch,
            "notes": feed.notes,
            "summary_stats": [
                make_summary_stat(
                    "Total Feed Cost" if is_admin else "Your Feed Cost Share",
                    format_money(feed.amount)
                    if is_admin
                    else metric_value(user_row, "Feed Cost Share", "Rs 0"),
                )
            ],
            "ownership_title": ownership_panel_title(),
            "ownership_breakdown": visible_ownership_rows(impact_rows),
        })

    for med in medicine_records:
        impact_rows = get_cost_impact_rows(
            med.batch,
            med.amount,
            "Medicine Cost Share",
        )
        user_row = current_user_row(impact_rows)

        log_items.append({
            "type": "medicine",
            "icon": "💊",
            "title": "Medicine Added",
            "date": med.entry_date,
            "batch": med.batch,
            "notes": med.notes,
            "summary_stats": [
                make_summary_stat(
                    "Medicine",
                    med.medicine_name,
                    med.get_medicine_type_display(),
                ),
                make_summary_stat(
                    "Total Medicine Cost" if is_admin else "Your Medicine Cost Share",
                    format_money(med.amount)
                    if is_admin
                    else metric_value(user_row, "Medicine Cost Share", "Rs 0"),
                ),
            ],
            "ownership_title": ownership_panel_title(),
            "ownership_breakdown": visible_ownership_rows(impact_rows),
        })

    for exp in expense_records:
        impact_rows = get_cost_impact_rows(
            exp.batch,
            exp.amount,
            "Expense Share",
        )
        user_row = current_user_row(impact_rows)

        log_items.append({
            "type": "expense",
            "icon": "🧾",
            "title": "Expense Added",
            "date": exp.expense_date,
            "batch": exp.batch,
            "notes": exp.description,
            "summary_stats": [
                make_summary_stat(
                    "Category",
                    exp.get_category_display(),
                ),
                make_summary_stat(
                    "Total Expense" if is_admin else "Your Expense Share",
                    format_money(exp.amount)
                    if is_admin
                    else metric_value(user_row, "Expense Share", "Rs 0"),
                ),
            ],
            "ownership_title": ownership_panel_title(),
            "ownership_breakdown": visible_ownership_rows(impact_rows),
        })

    log_items = sorted(
        log_items,
        key=lambda item: item["date"],
        reverse=True,
    )

    feed_status, _ = UserFeedStatus.objects.get_or_create(
        user=request.user
    )
    feed_status.last_seen_feed_time = timezone.now()
    feed_status.save()

    return render(request, "api/daily_log.html", {
        "is_admin": is_admin,
        "accessible_batches": accessible_batches,
        "log_items": log_items,
        "today": date.today(),
    })
