from django.contrib import messages
from django.views.decorators.http import require_http_methods

from datetime import date

from django.shortcuts import get_object_or_404, redirect, render

from django.contrib.auth.decorators import login_required

from api.models.sensor import (
    Batch,
    Device,
    MortalityRecord,
    SensorData,
    Shed,
    VaccineRecord,
    VaccineSchedule,
)
from api.models.investors import (
    InvestorAllocation,
    UserFeedStatus,
    FeedEntry,
    MedicineEntry,

)
from api.models.sales import (
    SaleRecord,
    Expense,
)

from api.models.investors import InvestorAllocation,  UserFeedStatus
from django.utils import timezone

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
            is_active=True
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
            messages.error(request, "This batch is closed. You cannot add mortality.")
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

    def get_user_share(batch):
        if batch.bird_count_initial <= 0:
            return 0

        if is_admin:
            allocated_investor_birds = sum(
                InvestorAllocation.objects.filter(
                    batch=batch
                ).values_list("birds_owned", flat=True)
            )

            admin_birds = batch.bird_count_initial - allocated_investor_birds
            return admin_birds / batch.bird_count_initial

        if is_investor:
            allocation = InvestorAllocation.objects.filter(
                batch=batch,
                investor=request.user.investor_profile
            ).first()

            if allocation:
                return allocation.birds_owned / batch.bird_count_initial

        return 0

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

    def build_owner_inputs_for_batch(batch):
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

        return owner_inputs

    def allocate_count_to_owners(batch, total_count, owner_inputs):
        total_count = int(total_count or 0)

        if (
                total_count <= 0
                or batch.bird_count_initial <= 0
                or not owner_inputs
        ):
            return [0] * len(owner_inputs)

        exact_values = []

        for owner in owner_inputs:
            exact_value = (
                    total_count
                    * owner["birds"]
                    / batch.bird_count_initial
            )

            exact_values.append(exact_value)

        allocated_values = [
            int(value)
            for value in exact_values
        ]

        remaining_count = total_count - sum(allocated_values)

        allocation_order = sorted(
            range(len(owner_inputs)),
            key=lambda index: (
                exact_values[index] - allocated_values[index],
                0 if owner_inputs[index]["is_admin_owner"] else 1
            ),
            reverse=True
        )

        for index in allocation_order[:remaining_count]:
            allocated_values[index] += 1

        return allocated_values

    def get_mortality_impact_rows(record):
        batch = record.batch
        owner_inputs = build_owner_inputs_for_batch(batch)

        previous_mortality_before_date = sum(
            MortalityRecord.objects.filter(
                batch=batch,
                date__lt=record.date
            ).values_list("count", flat=True)
        )

        previous_mortality_same_date = sum(
            MortalityRecord.objects.filter(
                batch=batch,
                date=record.date,
                id__lt=record.id
            ).values_list("count", flat=True)
        )

        previous_total_mortality = (
                previous_mortality_before_date
                + previous_mortality_same_date
        )

        after_total_mortality = previous_total_mortality + int(record.count or 0)

        before_allocations = allocate_count_to_owners(
            batch,
            previous_total_mortality,
            owner_inputs
        )

        after_allocations = allocate_count_to_owners(
            batch,
            after_total_mortality,
            owner_inputs
        )

        impact_rows = []

        for index, owner in enumerate(owner_inputs):
            if batch.bird_count_initial > 0:
                percentage = round(
                    (owner["birds"] / batch.bird_count_initial) * 100,
                    1
                )
            else:
                percentage = 0

            mortality_impact = (
                    after_allocations[index]
                    - before_allocations[index]
            )

            impact_rows.append({
                "name": owner["name"],
                "birds": owner["birds"],
                "percentage": percentage,
                "mortality_impact": mortality_impact,
                "is_admin_owner": owner["is_admin_owner"],
                "user_id": owner["user_id"],
            })

        return impact_rows

    for record in mortality_records:
        batch = record.batch

        impact_rows = get_mortality_impact_rows(record)

        investor_percentage = 0
        investor_mortality_impact = 0
        ownership_breakdown = []

        if is_admin:
            ownership_breakdown = impact_rows

        if is_investor and not is_admin:
            for owner_row in impact_rows:
                if owner_row["user_id"] == request.user.id:
                    investor_percentage = owner_row["percentage"]
                    investor_mortality_impact = owner_row["mortality_impact"]
                    break

        admin_mortality_impact = 0

        for owner_row in impact_rows:
            if owner_row["is_admin_owner"]:
                admin_mortality_impact = owner_row["mortality_impact"]
                break

        if is_admin:
            mortality_details = (
                f"Admin share mortality impact: "
                f"{admin_mortality_impact} birds"
            )
            mortality_amount = admin_mortality_impact

        else:
            mortality_details = (
                f"Your mortality impact: "
                f"{investor_mortality_impact} birds"
            )
            mortality_amount = investor_mortality_impact

        log_items.append({
            "type": "mortality",
            "icon": "⚰️",
            "title": "Mortality Recorded",
            "date": record.date,
            "batch": batch,
            "amount": mortality_amount,
            "details": mortality_details,
            "notes": record.notes,
            "total_mortality": record.count,
            "investor_percentage": investor_percentage,
            "investor_mortality_impact": investor_mortality_impact,
            "ownership_breakdown": ownership_breakdown,
        })

    for sale in sale_records:
        share = get_user_share(sale.batch)

        shared_birds = round(sale.birds_sold * share)
        shared_weight = round(float(sale.total_weight_kg) * share, 2)
        shared_revenue = round(float(sale.total_amount) * share, 2)

        if is_admin:
            label = "Admin sale share"
        else:
            label = "Your sale share"

        log_items.append({
            "type": "sale",
            "icon": "💰",
            "title": "Sale Recorded",
            "date": sale.sale_date,
            "batch": sale.batch,
            "amount": shared_revenue,
            "details": f"{label}: {shared_birds} birds, {shared_weight} kg, Rs {shared_revenue}",
            "notes": sale.notes,
        })

    for feed in feed_records:
        share = get_user_share(feed.batch)
        shared_amount = round(float(feed.amount) * share, 2)

        if is_admin:
            label = "Admin feed share"
        else:
            label = "Your feed share"

        log_items.append({
            "type": "feed",
            "icon": "🌾",
            "title": "Feed Added",
            "date": feed.entry_date,
            "batch": feed.batch,
            "amount": shared_amount,
            "details": f"{label}: Rs {shared_amount:,.0f}",
            "notes": feed.notes,
        })

    for med in medicine_records:
        share = get_user_share(med.batch)
        shared_amount = round(float(med.amount) * share, 2)

        if is_admin:
            label = "Admin medicine share"
        else:
            label = "Your medicine share"

        log_items.append({
            "type": "medicine",
            "icon": "💊",
            "title": "Medicine Added",
            "date": med.entry_date,
            "batch": med.batch,
            "amount": shared_amount,
            "details": f"{label}: Rs {shared_amount:,.0f}",
            "notes": med.notes,
        })

    for exp in expense_records:
        share = get_user_share(exp.batch)
        shared_amount = round(float(exp.amount) * share, 2)

        if is_admin:
            label = "Admin expense share"
        else:
            label = "Your expense share"

        log_items.append({
            "type": "expense",
            "icon": "🧾",
            "title": "Expense Added",
            "date": exp.expense_date,
            "batch": exp.batch,
            "amount": shared_amount,
            "details": f"{label}: {exp.get_category_display()} - Rs {shared_amount:,.0f}",
            "notes": exp.description,
        })

    log_items = sorted(
        log_items,
        key=lambda x: x["date"],
        reverse=True
    )

    feed_status, created = UserFeedStatus.objects.get_or_create(
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