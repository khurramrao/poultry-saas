"""Separate admin-only costs. Never linked to a salesperson's commission."""
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Sum
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from api.models.egg_pos_admin_expense import EggPOSAdminExpense
from api.services.egg_pos import can_manage
from api.services.egg_pos_admin_expenses import (
    sync_admin_expense, sync_admin_expense_void,
)


def _positive_money(value):
    try:
        amount = Decimal(str(value or "")).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Enter a valid expense amount.")
    if not amount.is_finite() or amount <= 0 or amount > Decimal("999999999999.99"):
        raise ValueError("Expense amount must be greater than zero and within the allowed limit.")
    return amount


def _validate_admin(request):
    # Staff POS access is NOT enough to create or reverse admin expenses.
    return bool(request.user.is_superuser or (request.user.is_staff and can_manage(request.user)))


@login_required
@require_http_methods(["GET", "POST"])
def admin_expenses(request):
    if not _validate_admin(request):
        return HttpResponseForbidden("Only Egg POS administrators can manage general expenses.")

    if request.method == "POST":
        try:
            if not request.user.check_password(request.POST.get("admin_password", "")):
                raise ValueError("Admin password is incorrect.")
            category = (request.POST.get("category") or "").strip()
            payment = (request.POST.get("payment_source") or "").strip()
            if category not in dict(EggPOSAdminExpense.CATEGORY_CHOICES):
                raise ValueError("Select a valid expense category.")
            if payment not in dict(EggPOSAdminExpense.PAYMENT_CHOICES):
                raise ValueError("Choose Main Cash or Bank / Digital Wallet.")
            try:
                entry_date = date.fromisoformat(request.POST.get("expense_date", ""))
            except (ValueError, TypeError):
                raise ValueError("Enter a valid expense date.")
            if entry_date > timezone.localdate():
                raise ValueError("Expense date cannot be in the future.")
            amount = _positive_money(request.POST.get("amount"))
            try:
                token = uuid.UUID(request.POST.get("request_token", ""))
            except (TypeError, ValueError, AttributeError):
                raise ValueError("Your form has expired. Refresh and try again.")
            with transaction.atomic():
                # Prevent a double-click / browser retry creating two cash payments.
                existing = EggPOSAdminExpense.objects.filter(request_token=token).first()
                if existing:
                    messages.info(request, f"This expense was already saved as #{existing.pk}; no duplicate created.")
                    return redirect("egg_pos_admin_expenses")
                expense = EggPOSAdminExpense.objects.create(
                    expense_date=entry_date,
                    category=category,
                    amount=amount,
                    payment_source=payment,
                    paid_to=(request.POST.get("paid_to") or "").strip()[:160],
                    reference=(request.POST.get("reference") or "").strip()[:120],
                    notes=(request.POST.get("notes") or "").strip(),
                    request_token=token,
                    recorded_by=request.user,
                )
                sync_admin_expense(expense)
            kind = "fixed asset" if expense.is_capital_asset else "operating expense"
            messages.success(request, f"Admin {kind} Rs {amount:,.2f} saved to the ledger and cashbook.")
            return redirect("egg_pos_admin_expenses")
        except Exception as exc:
            messages.error(request, str(exc))

    rows = list(EggPOSAdminExpense.objects.select_related("recorded_by", "voided_by")[:200])
    totals = EggPOSAdminExpense.objects.filter(is_voided=False).values("category").annotate(total=Sum("amount"))
    operating = sum((r["total"] for r in totals if r["category"] != "equipment"), Decimal("0.00"))
    equipment = sum((r["total"] for r in totals if r["category"] == "equipment"), Decimal("0.00"))
    return render(request, "api/egg_pos_admin_expenses.html", {
        "rows": rows,
        "categories": EggPOSAdminExpense.CATEGORY_CHOICES,
        "payment_sources": EggPOSAdminExpense.PAYMENT_CHOICES,
        "today": timezone.localdate(),
        "request_token": str(uuid.uuid4()),
        "operating_total": operating,
        "equipment_total": equipment,
    })


@login_required
@require_POST
def void_admin_expense(request, expense_id):
    if not _validate_admin(request):
        return HttpResponseForbidden("Only Egg POS administrators can void general expenses.")
    if not request.user.check_password(request.POST.get("admin_password", "")):
        messages.error(request, "Admin password is incorrect; expense unchanged.")
        return redirect("egg_pos_admin_expenses")
    reason = (request.POST.get("reason") or "").strip()
    if len(reason) < 12:
        messages.error(request, "Please enter a meaningful void reason (at least 12 characters).")
        return redirect("egg_pos_admin_expenses")
    with transaction.atomic():
        expense = get_object_or_404(EggPOSAdminExpense.objects.select_for_update(), pk=expense_id)
        if expense.is_voided:
            messages.warning(request, "This expense has already been voided.")
            return redirect("egg_pos_admin_expenses")
        expense.is_voided = True
        expense.voided_at = timezone.now()
        expense.voided_by = request.user
        expense.void_reason = reason[:500]
        expense.save(update_fields=["is_voided", "voided_at", "voided_by", "void_reason"])
        sync_admin_expense_void(expense)
    messages.success(request, f"Admin expense #{expense.pk} voided; reversing journal created.")
    return redirect("egg_pos_admin_expenses")
