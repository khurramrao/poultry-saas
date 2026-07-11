import json





from datetime import date, timedelta, time as clock_time
from zoneinfo import ZoneInfo
from django.utils import timezone
from django.conf import settings
from django.db.models import Sum
from django.contrib.auth.models import User

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from django.apps import apps
from django.core.exceptions import ObjectDoesNotExist
from django.contrib import messages
import json
import logging
from urllib.parse import urlencode
from urllib.request import Request, urlopen
logger = logging.getLogger(__name__)

from django.core.cache import cache
from decimal import Decimal


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

from api.models.investors import InvestorProfile, InvestorAllocation, BatchCost, UserProfile
from api.models.investors import (
    UserActivityLog,
    InvestorAllocation,
    UserFeedStatus,
    FeedEntry,
    MedicineEntry,

)
from api.models.sales import (
    SaleRecord,
    ChickCostEntry,
    Expense,
)
from django.utils import timezone

from django.utils.timesince import timesince
from django.contrib.auth import logout
from django.views.decorators.http import require_POST


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


def get_dunyapur_weather():
    cache_key = "dunyapur_outdoor_weather"
    cached_weather = cache.get(cache_key)

    if cached_weather:
        return cached_weather

    try:
        params = {
            "latitude": 29.80,
            "longitude": 71.74,
            "current": "temperature_2m,relative_humidity_2m,weather_code",
            "timezone": "Asia/Karachi",
        }

        weather_url = (
            "https://api.open-meteo.com/v1/forecast?"
            + urlencode(params)
        )

        weather_request = Request(
            weather_url,
            headers={
                "User-Agent": "RayNoorFarmDashboard/1.0",
                "Accept": "application/json",
            },
        )

        with urlopen(weather_request, timeout=15) as response:
            weather_json = json.load(response)

        current = weather_json.get("current", {})

        temperature = current.get("temperature_2m")
        humidity = current.get("relative_humidity_2m")
        weather_code = current.get("weather_code", -1)

        if temperature is None:
            return None

        weather_map = {
            0: ("Clear", "fa-solid fa-sun"),
            1: ("Mostly Clear", "fa-solid fa-cloud-sun"),
            2: ("Partly Cloudy", "fa-solid fa-cloud-sun"),
            3: ("Cloudy", "fa-solid fa-cloud"),
            45: ("Foggy", "fa-solid fa-smog"),
            48: ("Foggy", "fa-solid fa-smog"),
            51: ("Light Drizzle", "fa-solid fa-cloud-rain"),
            53: ("Drizzle", "fa-solid fa-cloud-rain"),
            55: ("Heavy Drizzle", "fa-solid fa-cloud-rain"),
            61: ("Light Rain", "fa-solid fa-cloud-rain"),
            63: ("Rain", "fa-solid fa-cloud-rain"),
            65: ("Heavy Rain", "fa-solid fa-cloud-showers-heavy"),
            80: ("Rain Showers", "fa-solid fa-cloud-rain"),
            81: ("Rain Showers", "fa-solid fa-cloud-rain"),
            82: ("Heavy Showers", "fa-solid fa-cloud-showers-heavy"),
            95: ("Thunderstorm", "fa-solid fa-cloud-bolt"),
        }

        condition, icon = weather_map.get(
            weather_code,
            ("Outdoor Weather", "fa-solid fa-cloud")
        )

        weather_data = {
            "temperature": round(float(temperature), 1),
            "humidity": humidity,
            "condition": condition,
            "icon": icon,
        }

        cache.set(cache_key, weather_data, 15 * 60)

        return weather_data

    except Exception as error:
        logger.exception("Dunyapur weather request failed: %s", error)
        return None


def format_last_update(created_at):
    if not created_at:
        return "No sensor data"

    now = timezone.now()
    difference = now - created_at

    # Two days or older: show only total days
    if difference.days >= 2:
        return f"{difference.days} days ago"

    # Under two days: show normal relative time
    return f"{timesince(created_at, now)} ago"

@login_required
def dashboard(request, template_name="api/dashboard_v2.html"):
    is_admin = request.user.is_superuser or request.user.is_staff
    sheds = Shed.objects.all()
    profile_obj, created = UserProfile.objects.get_or_create(
        user=request.user
    )

    outdoor_weather = get_dunyapur_weather()

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

        farm_time = timezone.now().astimezone(
            ZoneInfo("Asia/Karachi")
        ).time()

        lights_scheduled_off = (
                farm_time >= clock_time(21, 0)  # 9:00 PM onward
                or farm_time < clock_time(5, 0)  # before 5:00 AM
        )

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

            if (
                    latest.ldr_raw is not None
                    and latest.ldr_raw > 1500
                    and not lights_scheduled_off
            ):
                shed_alerts.append("Low Light")

            if latest.sensor_error:
                shed_alerts.append("Sensor Error")

        if is_admin and has_batch_alert and not device_offline and latest:
            shed_alerts.append("Batch Attention Needed")

        temperature_status = "normal"

        if latest and not device_offline and latest.temperature is not None:
            if any("Temp High" in batch["alerts"] for batch in batch_summaries):
                temperature_status = "high"

            elif any("Temp Low" in batch["alerts"] for batch in batch_summaries):
                temperature_status = "low"

            elif any("No Temp Rule" in batch["alerts"] for batch in batch_summaries):
                temperature_status = "no_rule"

        dashboard_rows.append({
            "shed": shed,
            "devices": devices,
            "device": devices.first(),
            "latest": latest,
            "device_offline": device_offline,
            "temperature_status": temperature_status,
            "lights_scheduled_off": lights_scheduled_off,
            "latest_readings": latest_readings,
            "alerts": shed_alerts,
            "batches": batch_summaries,
            "total_birds_in_shed": total_birds_in_shed,
            "last_update_display": format_last_update(latest.created_at) if latest else "No sensor data",

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

    active_batch_count = accessible_batches.distinct().count()
    total_mortality = 0
    mortality_starting_birds = 0

    for batch in accessible_batches:
        batch_mortality = sum(
            MortalityRecord.objects.filter(batch=batch).values_list("count", flat=True)
        )

        if is_admin:
            total_mortality += batch_mortality
            mortality_starting_birds += batch.bird_count_initial

        else:
            allocation = InvestorAllocation.objects.filter(
                batch=batch,
                investor=request.user.investor_profile
            ).first()

            if allocation and batch.bird_count_initial > 0:
                investor_share = allocation.birds_owned / batch.bird_count_initial

                total_mortality += round(batch_mortality * investor_share)
                mortality_starting_birds += allocation.birds_owned

    if mortality_starting_birds > 0:
        mortality_percentage = round(
            (total_mortality / mortality_starting_birds) * 100,
            2
        )
    else:
        mortality_percentage = 0

    # --- INVESTOR OWNERSHIP KPI ---
    investor_owned_birds = 0
    investor_total_starting_birds = 0
    investor_ownership_percentage = 0

    if not is_admin and hasattr(request.user, "investor_profile"):
        investor_allocations = InvestorAllocation.objects.filter(
            investor=request.user.investor_profile,
            batch__in=accessible_batches
        ).select_related("batch")

        for allocation in investor_allocations:
            investor_owned_birds += allocation.birds_owned
            investor_total_starting_birds += allocation.batch.bird_count_initial

        if investor_total_starting_birds > 0:
            investor_ownership_percentage = round(
                (investor_owned_birds / investor_total_starting_birds) * 100,
                1
            )

    # --- SOLD + SALES KPI ---
    total_sold_kpi = 0
    sold_starting_birds = 0
    total_sales_kpi = Decimal("0.00")

    for batch in accessible_batches:
        batch_sold = sum(
            SaleRecord.objects.filter(batch=batch).values_list(
                "birds_sold",
                flat=True
            )
        )

        batch_sales = sum(
            (sale.total_amount for sale in SaleRecord.objects.filter(batch=batch)),
            0
        )

        if is_admin:
            total_sold_kpi += batch_sold
            sold_starting_birds += batch.bird_count_initial
            total_sales_kpi += batch_sales

        elif hasattr(request.user, "investor_profile"):
            allocation = InvestorAllocation.objects.filter(
                batch=batch,
                investor=request.user.investor_profile
            ).first()

            if allocation and batch.bird_count_initial > 0:
                share_ratio = allocation.birds_owned / batch.bird_count_initial

                total_sold_kpi += round(batch_sold * share_ratio)
                sold_starting_birds += allocation.birds_owned
                total_sales_kpi += (
                        Decimal(str(batch_sales))
                        * Decimal(allocation.birds_owned)
                        / Decimal(batch.bird_count_initial)
                )

    if sold_starting_birds > 0:
        sold_percentage = round(
            (total_sold_kpi / sold_starting_birds) * 100,
            2
        )
    else:
        sold_percentage = 0

    # --- DAILY LOG NOTIFICATION COUNT ---
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
                MortalityRecord.objects.filter(
                    batch__in=accessible_batches
                ).count()
                + SaleRecord.objects.filter(
            batch__in=accessible_batches
        ).count()
                + FeedEntry.objects.filter(
            batch__in=accessible_batches
        ).count()
                + MedicineEntry.objects.filter(
            batch__in=accessible_batches
        ).count()
                + Expense.objects.filter(
            batch__in=accessible_batches
        ).count()
        )

    return render(request, template_name, {
        "dashboard_rows": dashboard_rows,
        "farm_total_birds": farm_total_birds,
        "is_admin": is_admin,
        "new_log_count": new_feed_count,
        "active_batch_count": active_batch_count,
        "total_mortality": total_mortality,
        "mortality_percentage": mortality_percentage,

        "investor_owned_birds": investor_owned_birds,
        "investor_total_starting_birds": investor_total_starting_birds,
        "investor_ownership_percentage": investor_ownership_percentage,

        "total_sold_kpi": total_sold_kpi,
        "sold_percentage": sold_percentage,
        "total_sales_kpi": total_sales_kpi,
        "user_profile": profile_obj,
        "outdoor_weather": outdoor_weather,
    })

@login_required
def dashboard_v2(request):
    return dashboard(request, template_name="api/dashboard_v2.html")

@login_required
def vaccine_records(request, batch_id):
    batch = get_object_or_404(Batch, id=batch_id)
    today = timezone.localdate()

    # Automatically update old due vaccines to overdue
    VaccineRecord.objects.filter(
        batch=batch,
        status="due",
        due_date__lt=today,
        given_date__isnull=True
    ).update(status="overdue")

    records = VaccineRecord.objects.filter(batch=batch).order_by(
        "due_date",
        "scheduled_day",
        "vaccine_name"
    )

    is_admin = request.user.is_superuser or request.user.is_staff

    return render(request, "api/vaccine_records.html", {
        "batch": batch,
        "records": records,
        "today": today,
        "is_admin": is_admin,
    })


@login_required
@require_POST
def mark_vaccine_done(request, record_id):
    is_admin = request.user.is_superuser or request.user.is_staff

    if not is_admin:
        messages.error(request, "Only admin can update vaccine records.")
        return redirect("dashboard")

    record = get_object_or_404(VaccineRecord, id=record_id)

    record.status = "done"
    record.given_date = timezone.localdate()
    record.save(update_fields=["status", "given_date"])

    messages.success(
        request,
        f"{record.vaccine_name} marked as given successfully."
    )

    return redirect("vaccine_records", batch_id=record.batch.id)


@login_required
def ownership_shares(request):
    if not (request.user.is_superuser or request.user.is_staff):
        return redirect("dashboard")

    active_batches = Batch.objects.filter(
        is_active=True
    ).select_related("shed").order_by(
        "shed__name", "-start_date", "batch_number"
    )

    ownership_batches = []

    for batch in active_batches:
        total_mortality = sum(
            MortalityRecord.objects.filter(
                batch=batch
            ).values_list("count", flat=True)
        )

        total_sold = sum(
            SaleRecord.objects.filter(
                batch=batch
            ).values_list("birds_sold", flat=True)
        )

        current_birds = (
            batch.bird_count_initial
            - total_mortality
            - total_sold
        )

        # ---------- COGS ----------
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

        electricity_cost = Expense.objects.filter(
            batch=batch,
            category="electricity"
        ).aggregate(total=Sum("amount"))["total"] or 0

        total_cogs = (
            chick_cost
            + carriage_cost
            + feed_cost
            + medicine_cost
            + electricity_cost
        )

        allocations = list(
            InvestorAllocation.objects.filter(
                batch=batch
            ).select_related("investor__user")
        )

        allocated_investor_birds = sum(
            allocation.birds_owned
            for allocation in allocations
        )

        owners = []

        def build_owner_row(name, start_birds, share_ratio, is_admin_owner=False):
            owner_mortality = round(total_mortality * share_ratio)
            owner_sold = round(total_sold * share_ratio)
            owner_current = start_birds - owner_mortality - owner_sold

            chick_share = round(float(chick_cost) * share_ratio, 2)
            feed_share = round(float(feed_cost) * share_ratio, 2)
            medicine_share = round(float(medicine_cost) * share_ratio, 2)
            electricity_share = round(float(electricity_cost) * share_ratio, 2)
            carriage_share = round(float(carriage_cost) * share_ratio, 2)

            total_cogs_share = round(
                chick_share
                + feed_share
                + medicine_share
                + electricity_share
                + carriage_share,
                2
            )

            return {
                "name": name,
                "share_percentage": round(share_ratio * 100, 1),
                "start_birds": start_birds,
                "mortality": owner_mortality,
                "sold": owner_sold,
                "current": owner_current,
                "chick_cost": chick_share,
                "feed_cost": feed_share,
                "medicine_cost": medicine_share,
                "electricity_cost": electricity_share,
                "carriage_cost": carriage_share,
                "total_cogs_share": total_cogs_share,
                "is_admin": is_admin_owner,
            }

        # ---------- ADMIN SHARE ----------
        admin_start_birds = (
            batch.bird_count_initial
            - allocated_investor_birds
        )

        if admin_start_birds > 0 and batch.bird_count_initial > 0:
            admin_share = admin_start_birds / batch.bird_count_initial

            owners.append(
                build_owner_row(
                    name="You (Admin)",
                    start_birds=admin_start_birds,
                    share_ratio=admin_share,
                    is_admin_owner=True
                )
            )

        # ---------- INVESTOR SHARES ----------
        for allocation in allocations:
            if batch.bird_count_initial <= 0:
                continue

            investor_share = (
                allocation.birds_owned
                / batch.bird_count_initial
            )

            investor_name = (
                allocation.investor.user.get_full_name().strip()
                or allocation.investor.user.username
            )

            owners.append(
                build_owner_row(
                    name=investor_name,
                    start_birds=allocation.birds_owned,
                    share_ratio=investor_share,
                    is_admin_owner=False
                )
            )

        mortality_percentage = 0
        if batch.bird_count_initial > 0:
            mortality_percentage = round(
                (total_mortality / batch.bird_count_initial) * 100,
                2
            )

        ownership_batches.append({
            "batch": batch,
            "start_birds": batch.bird_count_initial,
            "current_birds": current_birds,
            "total_mortality": total_mortality,
            "total_sold": total_sold,
            "mortality_percentage": mortality_percentage,

            "chick_cost": chick_cost,
            "feed_cost": feed_cost,
            "medicine_cost": medicine_cost,
            "electricity_cost": electricity_cost,
            "carriage_cost": carriage_cost,
            "total_cogs": total_cogs,

            "owners": owners,
        })

    return render(request, "api/ownership_shares.html", {
        "ownership_batches": ownership_batches,
        "is_admin": True,
    })

@login_required
def user_profile(request):
    is_admin = request.user.is_superuser or request.user.is_staff
    investor_profile = getattr(request.user, "investor_profile", None)

    profile_obj, created = UserProfile.objects.get_or_create(
        user=request.user
    )

    phone_number = ""
    if investor_profile:
        phone_number = investor_profile.phone_number or ""

    if request.method == "POST":
        first_name = request.POST.get("first_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()
        email = request.POST.get("email", "").strip()
        phone_number = request.POST.get("phone_number", "").strip()
        profile_image = request.FILES.get("profile_image")

        if email and User.objects.exclude(
            pk=request.user.pk
        ).filter(
            email__iexact=email
        ).exists():
            messages.error(
                request,
                "This email is already being used by another account."
            )

        elif profile_image and profile_image.size > 2 * 1024 * 1024:
            messages.error(
                request,
                "Profile picture must be smaller than 2 MB."
            )

        else:
            request.user.first_name = first_name
            request.user.last_name = last_name
            request.user.email = email
            request.user.save()

            if investor_profile:
                investor_profile.phone_number = phone_number
                investor_profile.save()

            if profile_image:
                profile_obj.profile_image = profile_image

            profile_obj.save()

            messages.success(request, "Profile updated successfully.")
            return redirect("user_profile")

    if is_admin:
        role_label = "Administrator"
    elif investor_profile:
        role_label = "Investor"
    else:
        role_label = "User"

    return render(request, "api/user_profile.html", {
        "account_user": request.user,
        "user_profile": profile_obj,
        "is_admin": is_admin,
        "is_investor": bool(investor_profile),
        "phone_number": phone_number,
        "role_label": role_label,
    })

@login_required
def user_activity(request):
    is_admin = request.user.is_superuser or request.user.is_staff

    if not is_admin:
        return redirect("dashboard")

    now = timezone.now()
    users = User.objects.select_related("activity_status").order_by("username")

    activity_rows = []

    for user in users:
        status = getattr(user, "activity_status", None)

        last_seen = None
        last_ip = ""
        is_online = False

        latest_event = UserActivityLog.objects.filter(
            user=user
        ).order_by("-timestamp").first()

        if status:
            last_seen = status.last_seen
            last_ip = status.last_ip or ""

            if last_seen and now - last_seen <= timedelta(minutes=5):
                is_online = True

        # If user clicked logout, show offline immediately
        if latest_event and latest_event.event_type == "logout":
            is_online = False

        activity_rows.append({
            "user": user,
            "is_online": is_online,
            "last_seen": last_seen,
            "last_login": user.last_login,
            "last_ip": last_ip,
            "latest_event": latest_event,
        })

    return render(request, "api/user_activity.html", {
        "activity_rows": activity_rows,
        "now": now,
    })

@require_POST
def logout_user(request):
    logout(request)
    return redirect("login")