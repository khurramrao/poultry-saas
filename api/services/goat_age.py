"""Calendar-based goat age and time-on-farm helpers."""
import calendar
from datetime import date

from django.core.exceptions import ValidationError
from django.utils import timezone


def _add_months(start, months):
    month_index = start.year * 12 + start.month - 1 + months
    year, month_index = divmod(month_index, 12)
    month = month_index + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def calendar_age(start, today=None):
    """Completed calendar months, then remaining days (including month ends)."""
    today = today or timezone.localdate()
    if start is None or start > today:
        return None
    total_months = (today.year - start.year) * 12 + today.month - start.month
    if _add_months(start, total_months) > today:
        total_months -= 1
    anniversary = _add_months(start, total_months)
    years, months = divmod(total_months, 12)
    return years, months, (today - anniversary).days


def age_label(start, today=None):
    parts = calendar_age(start, today)
    if parts is None:
        return "Unknown"
    years, months, days = parts
    if years:
        return f"{years} yr {months} mo {days} d"
    return f"{months} mo {days} d"


def goat_age_context(goat, today=None):
    today = today or timezone.localdate()
    born = goat.date_of_birth
    acquired = goat.purchase_date
    days = (today - acquired).days if acquired and acquired <= today else None
    return {
        "goat_age_label": age_label(born, today),
        "goat_has_birth_date": bool(born and born <= today),
        "goat_age_days": (today - born).days if born and born <= today else None,
        "goat_age_is_estimated": bool(born and goat.date_of_birth_is_estimated),
        "goat_days_on_farm": days,
        "goat_days_on_farm_label": f"{days:,} days" if days is not None else "Unknown",
        "goat_age_as_of": today,
    }


def parse_goat_dates(data, acquisition_type="purchased"):
    """Validate real dates; never infer DOB from the purchase date."""
    try:
        acquired = date.fromisoformat(str(data.get("purchase_date") or ""))
    except (TypeError, ValueError):
        raise ValidationError({"purchase_date": "Enter a valid acquisition date."})
    raw_birth = str(data.get("date_of_birth") or "").strip()
    try:
        born = date.fromisoformat(raw_birth) if raw_birth else None
    except ValueError:
        raise ValidationError({"date_of_birth": "Enter a valid date of birth."})

    estimated = str(data.get("date_of_birth_is_estimated") or "").lower() in {
        "on", "true", "1", "yes",
    }
    today = timezone.localdate()
    errors = {}
    if acquired > today:
        errors["purchase_date"] = "Acquisition date cannot be in the future."
    if born and born > today:
        errors["date_of_birth"] = "Date of birth cannot be in the future."
    if born and born > acquired:
        errors["date_of_birth"] = "Date of birth cannot be after acquisition."
    if estimated and not born:
        errors["date_of_birth"] = "Enter an estimated date of birth, or untick Estimated."
    if acquisition_type == "born_on_farm":
        if not born:
            errors["date_of_birth"] = "A birth date is required for a goat born on the farm."
        elif born != acquired:
            errors["purchase_date"] = "For a farm-born goat, the entry date must match its birth date."
        if estimated:
            errors["date_of_birth_is_estimated"] = "A recorded farm birth must use its actual birth date."
    if errors:
        raise ValidationError(errors)
    return acquired, born, estimated
