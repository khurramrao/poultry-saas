import json
from datetime import date, timedelta
from django.utils import timezone
from django.conf import settings
from django.db.models import Sum

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from django.apps import apps
from django.core.exceptions import ObjectDoesNotExist

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

from api.models.investors import InvestorAllocation, InvestorProfile, UserFeedStatus, BatchCost
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
from django.utils import timezone


@csrf_exempt
def receive_sensor_data(request):
    if request.method != "POST":
        return JsonResponse({"status": "error"}, status=405)

    try:
        data = json.loads(request.body)
        api_key = data.get("api_key")

        if api_key != settings.ESP32_API_KEY:
            return JsonResponse({
                "status": "error",
                "message": "Unauthorized"
            }, status=401)

        device_id = data.get("device_id")
        device = Device.objects.filter(device_id=device_id).first()

        if not device:
            return JsonResponse({
                "status": "error",
                "message": "Device not registered"
            })

        SensorData.objects.create(
            device=device,
            temperature=data.get("temperature"),
            humidity=data.get("humidity"),
            light_percent=data.get("light_percent", 0),
            ldr_raw=data.get("ldr_raw", 0),
            ammonia_raw=data.get("ammonia_raw", 0),
            sensor_error=data.get("sensor_error", False),
        )

        return JsonResponse({"status": "success"})

    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)})


def ensure_vaccine_records(batch, shed_type):
    schedules = VaccineSchedule.objects.filter(shed_type=shed_type)

    for schedule in schedules:
        if schedule.day_number < batch.starting_age_days:
            continue

        offset_days = schedule.day_number - batch.starting_age_days
        due_date = batch.start_date + timedelta(days=offset_days)

        VaccineRecord.objects.get_or_create(
            batch=batch,
            vaccine_name=schedule.vaccine_name,
            scheduled_day=schedule.day_number,
            defaults={
                "due_date": due_date,
                "status": "due",
                "notes": schedule.notes,
            }
        )


def update_vaccine_statuses(batch):
    today = date.today()
    VaccineRecord.objects.filter(
        batch=batch,
        status='due',
        due_date__lt=today
    ).update(status='overdue')


def build_batch_summary(batch):
    age_days = batch.starting_age_days + (date.today() - batch.start_date).days
    age_weeks = ((age_days - 1) // 7) + 1

    ensure_vaccine_records(batch, batch.shed.shed_type)
    update_vaccine_statuses(batch)

    total_mortality = sum(
        MortalityRecord.objects.filter(batch=batch).values_list('count', flat=True)
    )

    total_sold = sum(
        SaleRecord.objects.filter(batch=batch).values_list('birds_sold', flat=True)
    )

    current_birds = batch.bird_count_initial - total_mortality - total_sold

    mortality_percent = 0
    if batch.bird_count_initial > 0:
        mortality_percent = round((total_mortality / batch.bird_count_initial) * 100, 2)

    due_today = list(VaccineRecord.objects.filter(
        batch=batch,
        due_date=date.today(),
        status='due'
    ).order_by('due_date', 'scheduled_day', 'vaccine_name'))

    due_tomorrow = list(VaccineRecord.objects.filter(
        batch=batch,
        due_date=date.today() + timedelta(days=1),
        status='due'
    ).order_by('due_date', 'scheduled_day', 'vaccine_name'))

    overdue_vaccines = list(VaccineRecord.objects.filter(
        batch=batch,
        status='overdue'
    ).order_by('due_date', 'scheduled_day', 'vaccine_name'))

    return {
        "batch": batch,
        "age_days": age_days,
        "age_weeks": age_weeks,
        "total_mortality": total_mortality,
        "current_birds": current_birds,
        "mortality_percent": mortality_percent,
        "due_today": due_today,
        "due_tomorrow": due_tomorrow,
        "overdue_vaccines": overdue_vaccines,
        "total_sold": total_sold,
    }



@login_required
def dashboard(request):
    is_admin = request.user.is_superuser or request.user.is_staff
    sheds = Shed.objects.all()

    dashboard_rows = []
    farm_total_birds = 0

    for shed in sheds:
        devices = Device.objects.filter(shed=shed)

        latest = None
        latest_readings = []
        shed_alerts = []
        batch_summaries = []
        has_batch_alert = False
        device_offline = False

        for device in devices:
            device_latest = SensorData.objects.filter(device=device).order_by('-created_at').first()
            is_offline = False

            if device_latest:
                if timezone.now() - device_latest.created_at > timedelta(minutes=3):
                    is_offline = True
                    device_offline = True
                    if is_admin:
                        shed_alerts.append(f"{device.device_id} Offline")

                latest_readings.append({
                    "device": device,
                    "latest": device_latest,
                    "is_offline": is_offline,
                })

                if latest is None or device_latest.created_at > latest.created_at:
                    latest = device_latest
            else:
                device_offline = True
                if is_admin:
                    shed_alerts.append(f"{device.device_id} No Sensor Data")

                latest_readings.append({
                    "device": device,
                    "latest": None,
                    "is_offline": True,
                })

        if not devices and is_admin:
            shed_alerts.append("No Device Assigned")

        active_batches = Batch.objects.filter(
            shed=shed,
            is_active=True
        ).order_by('-start_date', 'batch_number')

        total_birds_in_shed = 0
        investor_has_birds_in_this_shed = False

        for batch in active_batches:
            batch_summary = build_batch_summary(batch)
            batch_cost = BatchCost.objects.filter(batch=batch).first()

            total_cogs = 0
            cost_per_starting_bird = 0
            cost_per_current_bird = 0

            if batch_cost:
                total_cogs = batch_cost.total_cogs

                if batch.bird_count_initial > 0:
                    cost_per_starting_bird = round(total_cogs / batch.bird_count_initial, 2)

                if batch_summary["current_birds"] > 0:
                    cost_per_current_bird = round(total_cogs / batch_summary["current_birds"], 2)

            base_mortality = batch_summary["total_mortality"]
            base_sold = batch_summary["total_sold"]
            total_reduction = base_mortality + base_sold

            allocated_investor_birds = 0
            investor_shares_list = []
            user_allocation = None

            all_allocations = InvestorAllocation.objects.filter(
                batch_id=batch.id
            ).select_related('investor__user')

            for alloc in all_allocations:
                allocated_investor_birds += alloc.birds_owned

                if alloc.investor and alloc.investor.user and alloc.investor.user.id == request.user.id:
                    user_allocation = alloc

                if batch.bird_count_initial > 0:
                    share_percentage = alloc.birds_owned / batch.bird_count_initial
                    share_mortality = round(base_mortality * share_percentage)
                    share_sold = round(base_sold * share_percentage)
                    share_current_birds = alloc.birds_owned - share_mortality - share_sold

                    investor_shares_list.append({
                        "name": alloc.investor.user.username if (alloc.investor and alloc.investor.user) else "Unknown",
                        "birds": alloc.birds_owned,
                        "percentage": round(share_percentage * 100, 1),
                        "mortality": share_mortality,
                        "sold": share_sold,
                        "current_birds": share_current_birds,
                    })

            admin_birds_initial = batch.bird_count_initial - allocated_investor_birds
            admin_percentage = 0
            admin_mortality = 0
            admin_sold = 0
            admin_current_birds = 0

            if batch.bird_count_initial > 0 and admin_birds_initial > 0:
                admin_share_percentage = admin_birds_initial / batch.bird_count_initial
                admin_percentage = round(admin_share_percentage * 100, 1)
                admin_mortality = round(base_mortality * admin_share_percentage)
                admin_sold = round(base_sold * admin_share_percentage)
                admin_current_birds = admin_birds_initial - admin_mortality - admin_sold

                investor_shares_list.insert(0, {
                    "name": "You (Admin)",
                    "birds": admin_birds_initial,
                    "percentage": admin_percentage,
                    "mortality": admin_mortality,
                    "sold": admin_sold,
                    "current_birds": admin_current_birds,
                })

            investor_percentage = 0
            current_birds = 0
            initial_allocated = 0
            display_mortality = 0
            display_sold = 0

            if is_admin:
                current_birds = batch.bird_count_initial - total_reduction
                initial_allocated = batch.bird_count_initial
                display_mortality = base_mortality
                display_sold = base_sold
                total_birds_in_shed += current_birds
            else:
                if user_allocation and batch.bird_count_initial > 0:
                    investor_has_birds_in_this_shed = True

                    investor_share_percentage = user_allocation.birds_owned / batch.bird_count_initial
                    investor_percentage = round(investor_share_percentage * 100, 1)

                    display_mortality = round(base_mortality * investor_share_percentage)
                    display_sold = round(base_sold * investor_share_percentage)

                    current_birds = user_allocation.birds_owned - display_mortality - display_sold
                    initial_allocated = user_allocation.birds_owned

                    total_birds_in_shed += current_birds
                else:
                    continue

            batch_alerts = []
            age_days = batch_summary["age_days"]

            if latest and not device_offline:
                if latest.temperature is not None:
                    temp_rule = TemperatureRule.objects.filter(
                        shed_type=shed.shed_type,
                        min_age_days__lte=age_days,
                        max_age_days__gte=age_days
                    ).first()

                    if temp_rule:
                        if latest.temperature < temp_rule.low_temp:
                            batch_alerts.append("Temp Low")
                        elif latest.temperature > temp_rule.high_temp:
                            batch_alerts.append("Temp High")
                    else:
                        if is_admin:
                            batch_alerts.append("No Temp Rule")

                if latest.humidity is not None:
                    if latest.humidity < 45:
                        batch_alerts.append("Humidity Low")
                    elif latest.humidity > 70:
                        batch_alerts.append("Humidity High")

            if device_offline:
                batch_summary["alerts"] = ["Device Offline"]
            elif not latest:
                batch_summary["alerts"] = ["No Sensor Data"]
            else:
                batch_summary["alerts"] = batch_alerts

            if batch_summary["alerts"]:
                has_batch_alert = True

            batch_summary["current_birds"] = current_birds
            batch_summary["initial_birds_allocated"] = initial_allocated
            batch_summary["investor_percentage"] = investor_percentage
            batch_summary["investor_shares"] = investor_shares_list
            batch_summary["display_mortality"] = display_mortality
            batch_summary["display_sold"] = display_sold
            batch_summary["admin_birds_initial"] = admin_birds_initial
            batch_summary["admin_percentage"] = admin_percentage
            batch_summary["admin_mortality"] = admin_mortality
            batch_summary["admin_sold"] = admin_sold
            batch_summary["admin_current_birds"] = admin_current_birds
            batch_summary["batch_cost"] = batch_cost
            batch_summary["total_cogs"] = total_cogs
            batch_summary["cost_per_starting_bird"] = cost_per_starting_bird
            batch_summary["cost_per_current_bird"] = cost_per_current_bird

            batch_summaries.append(batch_summary)

        if not is_admin and not investor_has_birds_in_this_shed:
            continue

        farm_total_birds += total_birds_in_shed

        if is_admin and latest and not device_offline:
            if latest.ammonia_raw is not None and latest.ammonia_raw > 600:
                shed_alerts.append("Ammonia High")
            if latest.ldr_raw is not None and latest.ldr_raw > 1500:
                shed_alerts.append("Low Light")
            if latest.sensor_error:
                shed_alerts.append("Sensor Error")

        if is_admin and has_batch_alert and not device_offline and latest:
            shed_alerts.append("Batch Attention Needed")

        dashboard_rows.append({
            "shed": shed,
            "devices": devices,
            "device": devices.first(),
            "latest": latest,
            "latest_readings": latest_readings,
            "alerts": shed_alerts,
            "batches": batch_summaries,
            "total_birds_in_shed": total_birds_in_shed,
        })

    # --- DAILY LOG NOTIFICATION COUNT ---
    feed_status, created = UserFeedStatus.objects.get_or_create(
        user=request.user
    )

    last_seen = feed_status.last_seen_feed_time

    if is_admin:
        accessible_batches = Batch.objects.filter(is_active=True)

    elif hasattr(request.user, "investor_profile"):
        investor_batch_ids = InvestorAllocation.objects.filter(
            investor=request.user.investor_profile
        ).values_list("batch_id", flat=True)

        accessible_batches = Batch.objects.filter(
            id__in=investor_batch_ids,
            is_active=True
        )
    else:
        accessible_batches = Batch.objects.none()

    if last_seen:
        mortality_count = MortalityRecord.objects.filter(
            batch__in=accessible_batches,
            created_at__gt=last_seen
        ).count()

        sale_count = SaleRecord.objects.filter(
            batch__in=accessible_batches,
            created_at__gt=last_seen
        ).count()

        feed_count = FeedEntry.objects.filter(
            batch__in=accessible_batches,
            created_at__gt=last_seen
        ).count()

        medicine_count = MedicineEntry.objects.filter(
            batch__in=accessible_batches,
            created_at__gt=last_seen
        ).count()

        expense_count = Expense.objects.filter(
            batch__in=accessible_batches,
            created_at__gt=last_seen
        ).count()

        new_feed_count = (
                mortality_count
                + sale_count
                + feed_count
                + medicine_count
                + expense_count
        )
    else:
        new_feed_count = (
                MortalityRecord.objects.filter(batch__in=accessible_batches).count()
                + SaleRecord.objects.filter(batch__in=accessible_batches).count()
                + FeedEntry.objects.filter(batch__in=accessible_batches).count()
                + MedicineEntry.objects.filter(batch__in=accessible_batches).count()
                + Expense.objects.filter(batch__in=accessible_batches).count()
        )

    return render(request, "api/dashboard.html", {
        "dashboard_rows": dashboard_rows,
        "farm_total_birds": farm_total_birds,
        "is_admin": is_admin,
        "new_log_count": new_feed_count,
    })


@login_required
def vaccine_records(request, batch_id):
    batch = get_object_or_404(Batch, id=batch_id)

    ensure_vaccine_records(batch, batch.shed.shed_type)
    update_vaccine_statuses(batch)

    records = VaccineRecord.objects.filter(batch=batch).order_by(
        'due_date',
        'scheduled_day',
        'vaccine_name'
    )

    return render(request, "api/vaccine_records.html", {
        "batch": batch,
        "records": records,
        "today": date.today(),
    })


def mark_vaccine_done(request, record_id):
    record = get_object_or_404(VaccineRecord, id=record_id)

    record.status = 'done'
    record.given_date = date.today()
    record.save()

    return redirect('vaccine_records', batch_id=record.batch.id)