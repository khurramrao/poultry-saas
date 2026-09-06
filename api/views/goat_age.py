"""Goat age/date editing. Only Admin can change recorded birth/acquisition dates."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from api.models.goats import Goat
from api.services.goat_age import parse_goat_dates


class GoatDatesForm(forms.Form):
    purchase_date = forms.DateField(
        label="Purchase / Entry Date",
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "class": "date-input"}),
    )
    date_of_birth = forms.DateField(
        label="Date of Birth",
        required=False,
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "class": "date-input"}),
    )
    date_of_birth_is_estimated = forms.BooleanField(
        label="Date of birth is estimated",
        required=False,
    )

    def __init__(self, *args, goat=None, **kwargs):
        self.goat = goat
        super().__init__(*args, **kwargs)
        today = timezone.localdate().isoformat()
        for name in ("purchase_date", "date_of_birth"):
            self.fields[name].widget.attrs["max"] = today

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned
        try:
            acquired, born, estimated = parse_goat_dates(
                cleaned, self.goat.acquisition_type
            )
        except ValidationError as exc:
            raise exc
        cleaned.update({
            "purchase_date": acquired,
            "date_of_birth": born,
            "date_of_birth_is_estimated": estimated,
        })
        return cleaned


@login_required
@require_http_methods(["GET", "POST"])
def edit_goat_dates(request, goat_id):
    if not (request.user.is_superuser or request.user.is_staff):
        messages.error(request, "Only Admin can edit goat dates.")
        return redirect("goat_dashboard")

    goat = get_object_or_404(Goat, pk=goat_id)
    initial = {
        "purchase_date": goat.purchase_date,
        "date_of_birth": goat.date_of_birth,
        "date_of_birth_is_estimated": goat.date_of_birth_is_estimated,
    }
    form = GoatDatesForm(
        request.POST if request.method == "POST" else None,
        goat=goat,
        initial=initial,
    )

    if request.method == "POST" and form.is_valid():
        goat.purchase_date = form.cleaned_data["purchase_date"]
        goat.date_of_birth = form.cleaned_data["date_of_birth"]
        goat.date_of_birth_is_estimated = form.cleaned_data["date_of_birth_is_estimated"]
        try:
            goat.full_clean()
        except ValidationError as exc:
            if hasattr(exc, "message_dict"):
                for field, errors in exc.message_dict.items():
                    target = field if field in form.fields else None
                    for message in errors:
                        form.add_error(target, message)
            else:
                form.add_error(None, exc)
        else:
            goat.save(update_fields=[
                "purchase_date", "date_of_birth",
                "date_of_birth_is_estimated", "updated_at",
            ])
            messages.success(request, f"Dates updated for {goat.goat_code}.")
            return redirect("goat_detail", goat_id=goat.pk)

    return render(request, "api/edit_goat_dates.html", {
        "goat": goat,
        "form": form,
        "today": timezone.localdate(),
    })
