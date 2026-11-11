"""Retail inventory damage entry, history and admin-only reversal."""
from uuid import uuid4

from django.contrib import messages
from django.contrib.auth import authenticate
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Sum
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from api.models.egg_pos import EggPOSInventoryLot, EggPOSInventoryWriteOff
from api.services.egg_pos import can_manage
from api.services.egg_pos_wastage import record_writeoff, void_writeoff


def _only_management(request):
    return bool(request.user.is_active and can_manage(request.user))


@login_required
@require_http_methods(["GET", "POST"])
def wastage(request):
    if not _only_management(request):
        return HttpResponseForbidden("Only Retail POS management can manage inventory damage.")
    form = {
        "lot_id": "", "quantity": "", "classification": "routine",
        "reason": "breakage", "notes": "", "submission_token": uuid4().hex,
    }
    if request.method == "POST":
        for field in form:
            if field in request.POST:
                form[field] = request.POST[field]
        try:
            writeoff = record_writeoff(
                lot_id=form["lot_id"], quantity=form["quantity"],
                classification=form["classification"], reason=form["reason"],
                notes=form["notes"], submission_token=form["submission_token"],
                actor=request.user,
            )
        except (ValueError, TypeError) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"{writeoff.reference}: {writeoff.quantity} units written off at Rs {writeoff.total_cost:,.2f}. No cash transaction was created.")
            return redirect("egg_pos_wastage")

    lots = (EggPOSInventoryLot.objects.filter(quantity_remaining__gt=0)
            .select_related("product", "supplier", "farm_batch")
            .order_by("product__category", "product__name", "received_date", "pk"))
    records = list(EggPOSInventoryWriteOff.objects.select_related(
        "lot__product", "recorded_by", "voided_by",
    ).order_by("-created_at", "-id")[:100])
    active = EggPOSInventoryWriteOff.objects.filter(is_voided=False)
    routine_loss = active.filter(classification="routine").aggregate(total=Sum("total_cost"))["total"] or 0
    exceptional_loss = active.filter(classification="exceptional").aggregate(total=Sum("total_cost"))["total"] or 0
    return render(request, "api/egg_pos_wastage.html", {
        "form": form, "lots": lots, "records": records,
        "classifications": EggPOSInventoryWriteOff.CLASSES,
        "reasons": EggPOSInventoryWriteOff.REASONS,
        "routine_loss": routine_loss, "exceptional_loss": exceptional_loss,
        "record_count": active.count(), "today": timezone.localdate(),
        "is_admin": bool(request.user.is_active and (request.user.is_superuser or request.user.is_staff)),
    })


@login_required
@require_POST
def void_wastage(request, writeoff_id):
    if not request.user.is_active or not (request.user.is_superuser or request.user.is_staff):
        return HttpResponseForbidden("Only an administrator can reverse a write-off.")
    writeoff = get_object_or_404(EggPOSInventoryWriteOff, pk=writeoff_id)
    cache_key = f"retail:writeoff:password:{request.user.pk}"
    fails = int(cache.get(cache_key, 0) or 0)
    if fails >= 5:
        messages.error(request, "Too many incorrect passwords. Try again in 15 minutes.")
        return redirect("egg_pos_wastage")
    verified = authenticate(request, username=request.user.get_username(), password=request.POST.get("admin_password") or "")
    if verified is None or verified.pk != request.user.pk:
        cache.set(cache_key, fails + 1, 15 * 60)
        messages.error(request, "Admin password incorrect. Inventory was not changed.")
        return redirect("egg_pos_wastage")
    cache.delete(cache_key)
    try:
        void_writeoff(writeoff_id=writeoff.pk, actor=request.user, reason=request.POST.get("void_reason", ""))
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{writeoff.reference} reversed. Stock restored; the reversing journal preserves the original audit trail.")
    return redirect("egg_pos_wastage")
