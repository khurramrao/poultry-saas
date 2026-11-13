"""Audited admin-expense correction; does not mutate the original journal."""
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from api.models.egg_pos_admin_expense import EggPOSAdminExpense
from api.services.egg_pos import can_manage
from api.services.egg_pos_admin_expenses import sync_admin_expense, sync_admin_expense_void


@login_required
@require_POST
def correct_admin_expense(request, expense_id):
    user = request.user
    if not (user.is_superuser or (user.is_staff and can_manage(user))):
        return HttpResponseForbidden('Only authorized POS administrators can correct expenses.')
    if not user.has_usable_password() or not user.check_password(request.POST.get('admin_password') or ''):
        messages.error(request, 'Incorrect admin password. Expense was not changed.')
        return redirect('egg_pos_admin_expenses')
    reason = (request.POST.get('reason') or '').strip()
    if len(reason) < 12:
        messages.error(request, 'Explain the correction (at least 12 characters).')
        return redirect('egg_pos_admin_expenses')
    try:
        category = (request.POST.get('category') or '').strip()
        payment = (request.POST.get('payment_source') or '').strip()
        if category not in dict(EggPOSAdminExpense.CATEGORY_CHOICES) or payment not in dict(EggPOSAdminExpense.PAYMENT_CHOICES):
            raise ValueError('Choose a valid expense category and payment method.')
        corrected_date = date.fromisoformat(request.POST.get('expense_date') or '')
        if corrected_date > timezone.localdate():
            raise ValueError('Expense date cannot be in the future.')
        try:
            amount = Decimal(str(request.POST.get('amount'))).quantize(Decimal('0.01'))
        except (InvalidOperation, ValueError, TypeError):
            raise ValueError('Enter a valid correction amount.')
        if not amount.is_finite() or amount <= 0 or amount > Decimal('999999999999.99'):
            raise ValueError('Correction amount must be positive.')
        with transaction.atomic():
            original = get_object_or_404(EggPOSAdminExpense.objects.select_for_update(), pk=expense_id)
            if original.is_voided:
                raise ValueError('This expense was already voided or corrected; no duplicate correction was created.')
            replacement = EggPOSAdminExpense.objects.create(
                expense_date=corrected_date, category=category, amount=amount,
                payment_source=payment,
                paid_to=(request.POST.get('paid_to') or '').strip()[:160],
                reference=(request.POST.get('reference') or '').strip()[:120],
                notes=(request.POST.get('notes') or '').strip(),
                recorded_by=user,
            )
            original.is_voided = True
            original.voided_at = timezone.now()
            original.voided_by = user
            original.void_reason = f'[CORRECTION AEXP-{replacement.pk}] {reason}'[:500]
            original.save(update_fields=['is_voided', 'voided_at', 'voided_by', 'void_reason'])
            # The correction reversal is posted on the original expense's date.
            # Ordinary voids remain posted on their actual reversal date.
            sync_admin_expense_void(original)
            sync_admin_expense(replacement)
        messages.success(request, f'Original expense #{expense_id} preserved and reversed; corrected expense #{replacement.pk} posted.')
    except (ValueError, TypeError) as exc:
        messages.error(request, str(exc))
    return redirect('egg_pos_admin_expenses')
