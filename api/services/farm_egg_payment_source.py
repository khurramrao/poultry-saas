"""Validate how a Layer flock cost is paid, without duplicating an expense.

The canonical cost row records source and is read directly by cashbook. There
is deliberately no second manual cash movement created for these payments.
"""
from decimal import Decimal, InvalidOperation
from django.core.exceptions import ValidationError
from django.utils import timezone
from api.models.sales import FARM_EXPENSE_PAYMENT_CHOICES

VALID_SOURCES = frozenset(key for key, _ in FARM_EXPENSE_PAYMENT_CHOICES)


def validate_expense_payment(request, *, batch, amount, expense_date):
    source = (request.POST.get("payment_source") or "outside").strip()
    if source not in VALID_SOURCES:
        raise ValidationError("Select a valid Paid From option.")
    try:
        money = Decimal(str(amount)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError("Enter a valid expense amount.")
    if not money.is_finite() or money <= 0:
        raise ValidationError("Expense amount must be greater than zero.")
    if expense_date > timezone.localdate():
        raise ValidationError("Payment date cannot be in the future.")
    if source in {"farm_cash", "farm_bank"}:
        if getattr(batch.shed, "shed_type", "") != "layer":
            raise ValidationError("Farm Egg Management funds can only pay Layer flock expenses.")
        if not (request.user.is_staff or request.user.is_superuser):
            raise ValidationError("Only an admin can spend Farm Egg Management funds.")
        if not request.user.check_password(request.POST.get("admin_password") or ""):
            raise ValidationError("Enter your correct admin password to spend Farm Management funds.")
        from api.models.sensor import Batch
        from api.services.egg_farm_cashbook import get_farm_egg_cashbook
        layer_batches = list(Batch.objects.filter(shed__shed_type="layer"))
        balances = get_farm_egg_cashbook(layer_batches, max_rows=0)
        balance = balances["cash_balance" if source == "farm_cash" else "bank_balance"]
        if money > balance:
            where = "cash in hand" if source == "farm_cash" else "bank / wallet"
            raise ValidationError(
                f"Insufficient Farm Egg {where}: available Rs {balance:,.2f}, expense Rs {money:,.2f}. "
                "Record missing opening funds or receipts first; do not duplicate existing receipts."
            )
    return source, money
