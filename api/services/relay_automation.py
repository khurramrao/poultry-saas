from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from api.models.sensor import RelayChannel, SensorData


SENSOR_STALE_AFTER = timedelta(minutes=10)
DEFAULT_OVERRIDE_MINUTES = 60
ALLOWED_OVERRIDE_MINUTES = {15, 30, 60, 120}


def _local_now(now=None):
    value = now or timezone.now()
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_current_timezone())
    return timezone.localtime(value)


def _aware_combine(local_date, local_time):
    naive = datetime.combine(local_date, local_time)
    return timezone.make_aware(naive, timezone.get_current_timezone())


def _fmt_dt(value):
    if not value:
        return ""
    return timezone.localtime(value).strftime("%d %b %Y, %I:%M %p")


def _fmt_time(value):
    if not value:
        return "Not set"
    return value.strftime("%I:%M %p").lstrip("0")


def _minutes_label(minutes):
    minutes = int(minutes or 0)
    if minutes and minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours} hour" + ("s" if hours != 1 else "")
    return f"{minutes} min"


def _window_bounds(local_now, start_time, end_time):
    """Return (inside, active_start, active_end, next_start).

    Handles ordinary and overnight windows. Equal start/end means a 24-hour
    window so a relay can be sensor-controlled all day if desired.
    """
    if not start_time or not end_time:
        return False, None, None, None

    today = local_now.date()
    now_time = local_now.time().replace(tzinfo=None)

    if start_time == end_time:
        start_dt = _aware_combine(today, start_time)
        if local_now < start_dt:
            start_dt -= timedelta(days=1)
        end_dt = start_dt + timedelta(days=1)
        next_start = start_dt + timedelta(days=1)
        return True, start_dt, end_dt, next_start

    if start_time < end_time:
        start_today = _aware_combine(today, start_time)
        end_today = _aware_combine(today, end_time)

        if local_now < start_today:
            return False, None, None, start_today
        if local_now < end_today:
            return True, start_today, end_today, start_today + timedelta(days=1)
        return False, None, None, start_today + timedelta(days=1)

    # Overnight window, for example 18:00 -> 02:00.
    if now_time >= start_time:
        start_dt = _aware_combine(today, start_time)
        end_dt = _aware_combine(today + timedelta(days=1), end_time)
        return True, start_dt, end_dt, start_dt + timedelta(days=1)

    if now_time < end_time:
        start_dt = _aware_combine(today - timedelta(days=1), start_time)
        end_dt = _aware_combine(today, end_time)
        return True, start_dt, end_dt, _aware_combine(today, start_time)

    next_start = _aware_combine(today, start_time)
    return False, None, None, next_start


def _most_recent_anchor(local_now, start_time):
    anchor = _aware_combine(local_now.date(), start_time)
    if anchor > local_now:
        anchor -= timedelta(days=1)
    return anchor


def _latest_sensor(relay):
    return (
        SensorData.objects.filter(device=relay.device)
        .order_by("-created_at")
        .first()
    )


def _sensor_is_usable(sensor, local_now):
    if not sensor or sensor.sensor_error:
        return False
    created_at = timezone.localtime(sensor.created_at)
    return local_now - created_at <= SENSOR_STALE_AFTER


def _clear_expired_override(relay, local_now):
    if relay.manual_override_state is None:
        return False

    if relay.manual_override_until and relay.manual_override_until <= local_now:
        relay.manual_override_state = None
        relay.manual_override_started_at = None
        relay.manual_override_until = None
        relay.manual_override_allow_outside_schedule = False
        return True

    return False


def _save_command_if_changed(relay, target_state, local_now, source):
    update_fields = []

    if relay.desired_state != target_state:
        relay.desired_state = target_state
        relay.commanded_at = local_now
        relay.command_source = source
        relay.last_error = ""
        update_fields.extend(
            ["desired_state", "commanded_at", "command_source", "last_error"]
        )

    return update_fields


def _automation_target(relay, local_now, latest_sensor=None):
    mode = relay.automation_type

    if mode == "manual":
        return relay.desired_state, "Manual control only", "Manual control only", None

    if mode == "daily":
        inside, _start, end_dt, next_start = _window_bounds(
            local_now,
            relay.schedule_start_time,
            relay.schedule_end_time,
        )
        if inside:
            return True, "Daily schedule is active", f"OFF at {_fmt_dt(end_dt)}", None
        if next_start:
            return False, "Outside daily ON window", f"ON at {_fmt_dt(next_start)}", None
        return False, "Daily schedule is incomplete", "Set ON and OFF times", None

    if mode == "sensor_schedule":
        inside, _start, end_dt, next_start = _window_bounds(
            local_now,
            relay.schedule_start_time,
            relay.schedule_end_time,
        )

        if not inside:
            next_text = (
                f"Sensor automation starts at {_fmt_dt(next_start)}"
                if next_start
                else "Set the sensor schedule"
            )
            return False, "Outside sensor schedule - forced OFF", next_text, None

        sensor = latest_sensor or _latest_sensor(relay)
        sensor_value = None
        if sensor:
            sensor_value = int(sensor.light_percent or 0)

        if not _sensor_is_usable(sensor, local_now):
            return (
                False,
                "Sensor data missing or stale - safety OFF",
                f"Force OFF at {_fmt_dt(end_dt)}",
                sensor_value,
            )

        if sensor_value <= relay.sensor_on_threshold:
            return (
                True,
                f"Light {sensor_value}% <= {relay.sensor_on_threshold}%",
                f"Force OFF at {_fmt_dt(end_dt)}",
                sensor_value,
            )

        if sensor_value >= relay.sensor_off_threshold:
            return (
                False,
                f"Light {sensor_value}% >= {relay.sensor_off_threshold}%",
                f"Force OFF at {_fmt_dt(end_dt)}",
                sensor_value,
            )

        # Hysteresis band: preserve the current requested state.
        return (
            relay.desired_state,
            (
                f"Light {sensor_value}% is between thresholds; "
                "holding current state"
            ),
            f"Force OFF at {_fmt_dt(end_dt)}",
            sensor_value,
        )

    if mode == "repeating":
        if not relay.schedule_start_time:
            return False, "Repeating cycle start time is not set", "Set a cycle start time", None

        interval = max(int(relay.repeat_interval_minutes or 1), 1)
        duration = max(int(relay.run_duration_minutes or 1), 1)
        duration = min(duration, interval)

        if relay.schedule_end_time:
            inside, start_dt, end_dt, next_start = _window_bounds(
                local_now,
                relay.schedule_start_time,
                relay.schedule_end_time,
            )
            if not inside:
                return (
                    False,
                    "Outside repeating-cycle window",
                    f"Next run at {_fmt_dt(next_start)}" if next_start else "Waiting for next cycle",
                    None,
                )
            anchor = start_dt
        else:
            anchor = _most_recent_anchor(local_now, relay.schedule_start_time)
            end_dt = None

        elapsed_minutes = int((local_now - anchor).total_seconds() // 60)
        phase = elapsed_minutes % interval
        cycle_start = local_now - timedelta(minutes=phase, seconds=local_now.second, microseconds=local_now.microsecond)

        if phase < duration:
            off_at = cycle_start + timedelta(minutes=duration)
            if end_dt and off_at > end_dt:
                off_at = end_dt
            return True, "Repeating cycle is running", f"OFF at {_fmt_dt(off_at)}", None

        next_run = cycle_start + timedelta(minutes=interval)
        if end_dt and next_run >= end_dt:
            _inside, _s, _e, next_window_start = _window_bounds(
                end_dt + timedelta(seconds=1),
                relay.schedule_start_time,
                relay.schedule_end_time,
            )
            next_run = next_window_start

        return False, "Waiting for next repeating cycle", f"Next run at {_fmt_dt(next_run)}", None

    return False, "Unknown automation mode - safety OFF", "Review automation settings", None


def get_relay_automation_status(relay, now=None, latest_sensor=None, persist=True):
    local_now = _local_now(now)
    update_fields = []

    if _clear_expired_override(relay, local_now):
        update_fields.extend(
            [
                "manual_override_state",
                "manual_override_started_at",
                "manual_override_until",
                "manual_override_allow_outside_schedule",
            ]
        )

    target_state, reason, next_action, sensor_value = _automation_target(
        relay,
        local_now,
        latest_sensor=latest_sensor,
    )

    override_active = relay.manual_override_state is not None

    # Sensor + Schedule has a hard force-OFF boundary. A manual command that
    # started inside the normal window cannot continue beyond that boundary,
    # even if the schedule was edited while the override was active. A fresh
    # after-hours command is marked explicitly and remains temporary.
    if (
        override_active
        and relay.automation_type == "sensor_schedule"
        and not relay.manual_override_allow_outside_schedule
    ):
        inside, _s, _e, _n = _window_bounds(
            local_now,
            relay.schedule_start_time,
            relay.schedule_end_time,
        )
        if not inside:
            relay.manual_override_state = None
            relay.manual_override_started_at = None
            relay.manual_override_until = None
            relay.manual_override_allow_outside_schedule = False
            override_active = False
            update_fields.extend(
                [
                    "manual_override_state",
                    "manual_override_started_at",
                    "manual_override_until",
                    "manual_override_allow_outside_schedule",
                ]
            )
            target_state, reason, next_action, sensor_value = _automation_target(
                relay,
                local_now,
                latest_sensor=latest_sensor,
            )

    if override_active:
        target_state = bool(relay.manual_override_state)
        reason = "Temporary manual override"
        next_action = (
            f"Auto resumes at {_fmt_dt(relay.manual_override_until)}"
            if relay.manual_override_until
            else "Press Resume Auto"
        )
        source = "manual"
    else:
        source = "automation" if relay.automation_type != "manual" else relay.command_source

    if relay.automation_type != "manual" or override_active:
        update_fields.extend(
            _save_command_if_changed(relay, target_state, local_now, source)
        )

    if update_fields and persist:
        relay.save(update_fields=list(dict.fromkeys(update_fields)))

    return {
        "target_state": bool(target_state),
        "mode": relay.automation_type,
        "mode_label": relay.get_automation_type_display(),
        "override_active": override_active,
        "override_state": relay.manual_override_state,
        "override_until": relay.manual_override_until,
        "reason": reason,
        "next_action": next_action,
        "sensor_value": sensor_value,
        "schedule_start": relay.schedule_start_time,
        "schedule_end": relay.schedule_end_time,
        "schedule_start_display": _fmt_time(relay.schedule_start_time),
        "schedule_end_display": _fmt_time(relay.schedule_end_time),
        "repeat_interval_display": _minutes_label(relay.repeat_interval_minutes),
        "run_duration_display": _minutes_label(relay.run_duration_minutes),
    }


def evaluate_relay(relay, now=None, latest_sensor=None, persist=True):
    return get_relay_automation_status(
        relay,
        now=now,
        latest_sensor=latest_sensor,
        persist=persist,
    )


def evaluate_device_relays(device, latest_sensor=None, now=None):
    results = []
    relays = RelayChannel.objects.filter(
        device=device,
        is_enabled=True,
    ).order_by("channel_number")

    for relay in relays:
        results.append(
            (relay, evaluate_relay(relay, now=now, latest_sensor=latest_sensor))
        )

    return results


def apply_manual_override(relay, state, user, override_minutes=None, now=None):
    local_now = _local_now(now)
    state = bool(state)

    if relay.automation_type == "manual":
        relay.manual_override_state = None
        relay.manual_override_started_at = None
        relay.manual_override_until = None
        relay.manual_override_allow_outside_schedule = False
        relay.desired_state = state
        relay.commanded_at = local_now
        relay.changed_by = user
        relay.command_source = "manual"
        relay.last_error = ""
        relay.save(
            update_fields=[
                "manual_override_state",
                "manual_override_started_at",
                "manual_override_until",
                "manual_override_allow_outside_schedule",
                "desired_state",
                "commanded_at",
                "changed_by",
                "command_source",
                "last_error",
            ]
        )
        return get_relay_automation_status(relay, now=local_now)

    try:
        minutes = int(override_minutes or DEFAULT_OVERRIDE_MINUTES)
    except (TypeError, ValueError):
        minutes = DEFAULT_OVERRIDE_MINUTES
    if minutes not in ALLOWED_OVERRIDE_MINUTES:
        minutes = DEFAULT_OVERRIDE_MINUTES

    allow_outside = False
    override_until = local_now + timedelta(minutes=minutes)

    if relay.automation_type == "sensor_schedule":
        inside, _start, end_dt, _next_start = _window_bounds(
            local_now,
            relay.schedule_start_time,
            relay.schedule_end_time,
        )
        if inside and end_dt:
            # Manual command during the normal sensor window can never survive
            # the configured force-OFF time.
            override_until = min(override_until, end_dt)
            allow_outside = False
        else:
            # A fresh, explicit command after hours is allowed only temporarily.
            allow_outside = True

    relay.manual_override_state = state
    relay.manual_override_started_at = local_now
    relay.manual_override_until = override_until
    relay.manual_override_allow_outside_schedule = allow_outside
    relay.desired_state = state
    relay.commanded_at = local_now
    relay.changed_by = user
    relay.command_source = "manual"
    relay.last_error = ""
    relay.save(
        update_fields=[
            "manual_override_state",
            "manual_override_started_at",
            "manual_override_until",
            "manual_override_allow_outside_schedule",
            "desired_state",
            "commanded_at",
            "changed_by",
            "command_source",
            "last_error",
        ]
    )

    return get_relay_automation_status(relay, now=local_now)


def resume_auto(relay, now=None):
    relay.manual_override_state = None
    relay.manual_override_started_at = None
    relay.manual_override_until = None
    relay.manual_override_allow_outside_schedule = False
    relay.save(
        update_fields=[
            "manual_override_state",
            "manual_override_started_at",
            "manual_override_until",
            "manual_override_allow_outside_schedule",
        ]
    )
    return evaluate_relay(relay, now=now)


def validate_automation_values(data):
    mode = (data.get("automation_type") or "manual").strip()
    valid_modes = {choice[0] for choice in RelayChannel.AUTOMATION_TYPE_CHOICES}
    if mode not in valid_modes:
        raise ValueError("Invalid automation type.")

    def parse_time(value, label, required=False):
        value = (value or "").strip()
        if not value:
            if required:
                raise ValueError(f"{label} is required.")
            return None
        try:
            return datetime.strptime(value, "%H:%M").time()
        except ValueError as exc:
            raise ValueError(f"{label} must be a valid time.") from exc

    # Read the time fields belonging to the selected mode directly.
    # Do not depend on JavaScript to copy them into hidden canonical fields:
    # pages can contain other forms (for example sidebar/logout forms), which
    # made the old submit listener bind to the wrong form in some layouts.
    if mode == "daily":
        start_raw = data.get("daily_start")
        end_raw = data.get("daily_end")
        start_required = True
        end_required = True
    elif mode == "sensor_schedule":
        start_raw = data.get("sensor_schedule_start")
        end_raw = data.get("sensor_schedule_end")
        start_required = True
        end_required = True
    elif mode == "repeating":
        start_raw = data.get("repeating_start")
        end_raw = data.get("repeating_end")
        start_required = True
        end_required = False
    else:
        start_raw = None
        end_raw = None
        start_required = False
        end_required = False

    start_time = parse_time(
        start_raw,
        "Start time",
        required=start_required,
    )
    end_time = parse_time(
        end_raw,
        "End / force-OFF time",
        required=end_required,
    )

    try:
        sensor_on = int(data.get("sensor_on_threshold") or 60)
        sensor_off = int(data.get("sensor_off_threshold") or 70)
    except (TypeError, ValueError) as exc:
        raise ValueError("Light thresholds must be whole percentages.") from exc

    if not 0 <= sensor_on <= 100 or not 0 <= sensor_off <= 100:
        raise ValueError("Light thresholds must be between 0% and 100%.")
    if mode == "sensor_schedule" and sensor_off <= sensor_on:
        raise ValueError("OFF threshold must be higher than ON threshold to prevent relay chatter.")

    try:
        hours_value = (data.get("repeat_interval_hours") or "").strip()
        if hours_value:
            hours = Decimal(hours_value)
            interval = int((hours * Decimal("60")).quantize(Decimal("1")))
        else:
            interval = int(data.get("repeat_interval_minutes") or 240)
        duration = int(data.get("run_duration_minutes") or 30)
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise ValueError("Repeat interval and run duration must be valid numbers.") from exc

    if not 1 <= interval <= 1440:
        raise ValueError("Repeat interval must be between 1 and 1440 minutes.")
    if not 1 <= duration <= 1440:
        raise ValueError("Run duration must be between 1 and 1440 minutes.")
    if mode == "repeating" and duration > interval:
        raise ValueError("Run duration cannot be longer than the repeat interval.")

    return {
        "automation_type": mode,
        "schedule_start_time": start_time,
        "schedule_end_time": end_time,
        "sensor_on_threshold": sensor_on,
        "sensor_off_threshold": sensor_off,
        "repeat_interval_minutes": interval,
        "run_duration_minutes": duration,
    }
