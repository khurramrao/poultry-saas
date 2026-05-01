import json
from datetime import date, timedelta
from django.utils import timezone
from django.conf import settings
from django.db.models import Sum

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt

from api.models.sensor import (
    Batch,
    Device,
    MortalityRecord,
    SensorData,
    Shed,
    VaccineRecord,
    VaccineSchedule,

)
from django.contrib.auth.decorators import login_required

from api.models.temperature import TemperatureRule
from api.models.sales import SaleRecord


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
    sheds = Shed.objects.all()
    dashboard_rows = []
    farm_total_birds = 0  # ✅ farm total

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
                shed_alerts.append(f"{device.device_id} No Sensor Data")

                latest_readings.append({
                    "device": device,
                    "latest": None,
                    "is_offline": True,
                })

        if not devices:
            shed_alerts.append("No Device Assigned")

        active_batches = Batch.objects.filter(
            shed=shed,
            is_active=True
        ).order_by('-start_date', 'batch_number')

        total_birds_in_shed = 0

        for batch in active_batches:
            total_mortality = MortalityRecord.objects.filter(batch=batch).aggregate(
                total=Sum("count")
            )["total"] or 0

            current_birds = batch.bird_count_initial - total_mortality
            total_birds_in_shed += current_birds

        farm_total_birds += total_birds_in_shed  # ✅ add shed total to farm total

        for batch in active_batches:
            batch_summary = build_batch_summary(batch)
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

            batch_summaries.append(batch_summary)

        if latest and not device_offline:
            if latest.ammonia_raw is not None and latest.ammonia_raw > 600:
                shed_alerts.append("Ammonia High")

            if latest.ldr_raw is not None and latest.ldr_raw > 1500:
                shed_alerts.append("Low Light")

            if latest.sensor_error:
                shed_alerts.append("Sensor Error")

        if has_batch_alert and not device_offline and latest:
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

    return render(request, "api/dashboard.html", {
        "dashboard_rows": dashboard_rows,
        "farm_total_birds": farm_total_birds,  # ✅ send to HTML
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