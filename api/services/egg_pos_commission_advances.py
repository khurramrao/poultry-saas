"""Commission payments and advance allocations; preserves historical direct payments."""
from decimal import Decimal
from uuid import UUID

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from api.models.egg_pos import (
    EggPOSCommissionPeriod, EggPOSCommissionAdvancePayment,
    EggPOSCommissionAdvanceAllocation, EggPOSCommissionPayment,
)
from api.services.accounting import money, normal_account_balance
from api.services.egg_pos_accounting import (
    sync_commission_advance_payment, sync_commission_advance_allocation,
)

ZERO = Decimal("0.00")


def _validate_token(token):
    try:
        return str(UUID(str(token)))
    except (ValueError, AttributeError, TypeError):
        raise ValueError("Invalid payment request token. Refresh the form and retry.")


def _apply_oldest_unpaid_locked(salesperson):
    """Must be called inside transaction.atomic() with the salesperson row locked."""
    periods = list(
        EggPOSCommissionPeriod.objects.filter(salesperson=salesperson)
        .select_for_update()
        .prefetch_related("payments", "advance_allocations")
        .order_by("period_end", "period_start", "pk")
    )
    credits = list(
        EggPOSCommissionAdvancePayment.objects.filter(salesperson=salesperson)
        .select_for_update()
        .prefetch_related("allocations")
        .order_by("payment_date", "pk")
    )
    allocated_now = ZERO
    for period in periods:
        due = money(period.balance_due)
        if due <= ZERO:
            continue
        for credit in credits:
            remaining = money(credit.advance_remaining)
            if due <= ZERO or remaining <= ZERO:
                continue
            applied = min(due, remaining)
            row = EggPOSCommissionAdvanceAllocation.objects.create(
                payment=credit, commission=period,
                amount=applied,
                applied_date=max(timezone.localdate(), credit.payment_date, period.period_end),
            )
            # Refresh prefetched relations to prevent using stale advance balances.
            if hasattr(credit, "_prefetched_objects_cache"):
                credit._prefetched_objects_cache.pop("allocations", None)
            sync_commission_advance_allocation(row)
            allocated_now += applied
            due -= applied
    return money(allocated_now)


@transaction.atomic
def apply_commission_advances(salesperson_id):
    salesperson = get_user_model().objects.select_for_update().get(pk=salesperson_id)
    return _apply_oldest_unpaid_locked(salesperson)


@transaction.atomic
def record_commission_cash_payment(*, salesperson_id, amount, payment_date, payment_method,
                                   reference, notes, recorded_by, request_token):
    token = _validate_token(request_token)
    salesperson = get_user_model().objects.select_for_update().get(pk=salesperson_id, is_active=True)
    existing = EggPOSCommissionAdvancePayment.objects.filter(request_token=token).first()
    if existing:
        if (existing.salesperson_id != salesperson.id
                or money(existing.amount) != money(amount)
                or existing.payment_date != payment_date
                or existing.payment_method != payment_method):
            raise ValueError("This payment request token has already been used with different details. Refresh and retry.")
        return existing, ZERO, True
    amount = money(amount)
    if amount <= ZERO:
        raise ValueError("Commission payment must be greater than zero.")
    methods = dict(EggPOSCommissionPayment.PAYMENT_METHOD_CHOICES)
    if payment_method not in methods:
        raise ValueError("Select a valid payment method.")
    payment = EggPOSCommissionAdvancePayment.objects.create(
        salesperson=salesperson, payment_date=payment_date, amount=amount,
        payment_method=payment_method, reference=(reference or "").strip()[:120],
        notes=(notes or "").strip(), recorded_by=recorded_by, request_token=token,
    )
    sync_commission_advance_payment(payment)
    applied = _apply_oldest_unpaid_locked(salesperson)
    return payment, applied, False


def commission_account_summary(salesperson, *, today=None):
    today = today or timezone.localdate()
    month_start = today.replace(day=1)
    from django.db.models import Sum
    from api.models.egg_pos import EggPOSCommissionPeriod
    total_earned = money(EggPOSCommissionPeriod.objects.filter(salesperson=salesperson).aggregate(
        total=Sum("commission_amount"))["total"] or ZERO)
    legacy_paid = money(EggPOSCommissionPayment.objects.filter(commission__salesperson=salesperson)
                        .aggregate(total=Sum("amount"))["total"] or ZERO)
    advance_payments = EggPOSCommissionAdvancePayment.objects.filter(salesperson=salesperson)
    modern_paid = money(advance_payments.aggregate(total=Sum("amount"))["total"] or ZERO)
    paid_total = money(legacy_paid + modern_paid)
    posted_due = money(max(total_earned - paid_total, ZERO))
    advance_balance = money(max(paid_total - total_earned, ZERO))
    # Keep GL available for separate account reconciliation.
    month_received = money(
        (EggPOSCommissionPayment.objects.filter(
            commission__salesperson=salesperson, payment_date__range=(month_start, today))
         .aggregate(total=Sum("amount"))["total"] or ZERO)
        + (advance_payments.filter(payment_date__range=(month_start, today))
           .aggregate(total=Sum("amount"))["total"] or ZERO)
    )
    return {
        "total_earned": total_earned,
        "total_paid": paid_total,
        "posted_due": posted_due,
        "advance_balance": advance_balance,
        "month_received": month_received,
        "posted_liability_gl": normal_account_balance("2030", party_type="user", party_id=salesperson.pk),
        "advance_asset_gl": normal_account_balance("1150", party_type="user", party_id=salesperson.pk),
    }
