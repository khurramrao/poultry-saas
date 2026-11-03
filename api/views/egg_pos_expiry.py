"""Admin-only correction for recorded expiry dates on existing Egg POS FIFO lots.

No inventory quantities, FIFO allocations or accounting balances are changed here.
Changing a lot's date can change sellable stock because sellable-stock queries
exclude lots whose expiry_date is earlier than today.  Corrections are audited
in Django's built-in LogEntry table; no application migration is required.
"""
from datetime import date

from django.contrib import messages
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth import authenticate
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from api.models.egg_pos import EggPOSInventoryLot


def _admin(user):
    return bool(user.is_authenticated and user.is_active and (user.is_staff or user.is_superuser))


def _verify_admin_password(request):
    cache_key = f"eggpos:lot-expiry:auth:{request.user.pk}"
    failures = int(cache.get(cache_key, 0) or 0)
    if failures >= 5:
        messages.error(request, "Too many password attempts. Try again in 15 minutes.")
        return False
    submitted = request.POST.get("admin_password") or ""
    verified = authenticate(request, username=request.user.get_username(), password=submitted)
    if verified is None or verified.pk != request.user.pk:
        cache.set(cache_key, failures + 1, 15 * 60)
        messages.error(request, "Admin password incorrect. The expiry date was not changed.")
        return False
    cache.delete(cache_key)
    return True


@login_required
@require_http_methods(["GET", "POST"])
def correct_lot_expiry(request, lot_id):
    if not _admin(request.user):
        return HttpResponseForbidden("Only an administrator can correct an egg lot's expiry date.")

    lot = get_object_or_404(EggPOSInventoryLot.objects.select_related("product"), pk=lot_id)
    if request.method == "POST":
        if not _verify_admin_password(request):
            return redirect("egg_pos_lot_expiry", lot_id=lot_id)

        reason = (request.POST.get("reason") or "").strip()
        raw_date = (request.POST.get("expiry_date") or "").strip()
        if len(reason) < 12:
            messages.error(request, "Enter a meaningful correction reason (at least 12 characters).")
            return redirect("egg_pos_lot_expiry", lot_id=lot_id)
        if len(reason) > 1000:
            messages.error(request, "Correction reason is too long (maximum 1000 characters).")
            return redirect("egg_pos_lot_expiry", lot_id=lot_id)
        try:
            new_expiry = date.fromisoformat(raw_date)
        except ValueError:
            messages.error(request, "Select a valid expiry date. This form cannot remove an expiry date.")
            return redirect("egg_pos_lot_expiry", lot_id=lot_id)

        try:
            with transaction.atomic():
                # No select_related() on this FOR UPDATE query: nullable joins
                # can cause PostgreSQL to reject the row lock.
                locked = EggPOSInventoryLot.objects.select_for_update().get(pk=lot_id)
                if new_expiry < locked.received_date:
                    raise ValueError("Expiry cannot be earlier than the lot's received date.")
                old_expiry = locked.expiry_date
                if old_expiry == new_expiry:
                    raise ValueError("The entered expiry date is already saved for this lot.")
                if (
                    old_expiry is not None
                    and new_expiry > old_expiry
                    and request.POST.get("verified_date_correction") != "yes"
                ):
                    raise ValueError(
                        "For later expiry dates, confirm that you verified the original entry was incorrect. "
                        "Do not extend the real shelf life of eggs."
                    )

                locked.expiry_date = new_expiry
                locked.save(update_fields=["expiry_date"])
                # Built-in admin audit log is durable, queryable, and requires
                # no new schema/migration. The password itself is never logged.
                LogEntry.objects.create(
                    user=request.user,
                    content_type=ContentType.objects.get_for_model(EggPOSInventoryLot),
                    object_id=str(locked.pk),
                    object_repr=str(locked.lot_code)[:200],
                    action_flag=CHANGE,
                    change_message=(
                        f"Egg POS lot expiry corrected: {old_expiry or 'not recorded'} -> "
                        f"{new_expiry}. Reason: {reason}"
                    ),
                )
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("egg_pos_lot_expiry", lot_id=lot_id)
        messages.success(request, "Expiry date corrected and audit recorded. FIFO quantity and cost are unchanged.")
        return redirect("egg_pos_lot_expiry", lot_id=lot_id)

    content_type = ContentType.objects.get_for_model(EggPOSInventoryLot)
    history = (
        LogEntry.objects.filter(
            content_type=content_type,
            object_id=str(lot.pk),
            change_message__startswith="Egg POS lot expiry corrected:",
        ).select_related("user").order_by("-action_time", "-pk")[:20]
    )
    return render(request, "api/egg_pos_lot_expiry.html", {
        "lot": lot,
        "history": history,
        "is_expired": bool(lot.expiry_date and lot.expiry_date < timezone.localdate()),
    })
