"""Audited admin-loan asset ledger; keeps salary and owner loans separate."""
from decimal import Decimal, InvalidOperation
from uuid import UUID

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from api.models.egg_pos_admin_loan import EggPOSAdminLoanTransaction
from api.services.accounting import line, post_entry, normal_account_balance

ZERO = Decimal("0.00")
SOURCE_ACCOUNT = {"cash": "1000", "bank": "1010"}


def loan_outstanding(admin_id, *, as_of=None):
    qs = EggPOSAdminLoanTransaction.objects.filter(administrator_id=admin_id)
    if as_of is not None:
        qs = qs.filter(transaction_date__lte=as_of)
    amounts = qs.values("kind").annotate(amount=Sum("amount"))
    bucket = {r["kind"]: r["amount"] or ZERO for r in amounts}
    return (bucket.get("advance", ZERO) - bucket.get("repayment", ZERO)).quantize(Decimal("0.01"))


def sync_admin_loan(row):
    code = SOURCE_ACCOUNT[row.payment_source]
    person = {"party_type": "user", "party_id": row.administrator_id,
              "party_name": row.administrator.get_full_name().strip() or row.administrator.username}
    if row.kind == "advance":
        lines = [
            line("1320", debit=row.amount, description="Recoverable loan to administrator", **person),
            line(code, credit=row.amount, description="Company money paid to administrator"),
        ]
    else:
        lines = [
            line(code, debit=row.amount, description="Administrator repaid company"),
            line("1320", credit=row.amount, description="Admin loan receivable reduced", **person),
        ]
    return post_entry(
        source_key=f"egg_pos:admin_loan:{row.pk}", entry_date=row.transaction_date,
        reference=row.reference or f"ADMLN-{row.pk:06d}",
        memo=f"Administrator loan {row.get_kind_display()}: {row.administrator.username}",
        source_type="egg_pos_admin_loan", source_id=row.pk, created_by=row.recorded_by,
        lines=lines,
    )


@transaction.atomic
def record_admin_loan(*, administrator_id, kind, amount, payment_source, recorded_by,
                      request_token, transaction_date=None, reference="", notes=""):
    try:
        token = UUID(str(request_token))
        amount = Decimal(str(amount)).quantize(Decimal("0.01"))
    except (ValueError, InvalidOperation, TypeError, AttributeError):
        raise ValueError("Enter a valid amount and refresh the form if necessary.")
    if not amount.is_finite() or amount <= ZERO or amount > Decimal("999999999999.99"):
        raise ValueError("Enter a positive loan amount.")
    if kind not in {"advance", "repayment"} or payment_source not in SOURCE_ACCOUNT:
        raise ValueError("Invalid transaction type or cash/bank account.")
    # Only same-day recording is allowed here: it protects historical paid/payable balances
    # from silently changing through backdated repayments.
    today = timezone.localdate()
    transaction_date = transaction_date or today
    if transaction_date != today:
        raise ValueError("Admin loan transactions must use today's date. Historical corrections require reconciliation.")
    user = get_user_model().objects.select_for_update().get(pk=administrator_id, is_active=True)
    from api.services.egg_pos import can_manage
    if not (user.is_superuser or (user.is_staff and can_manage(user))):
        raise ValueError("Select an active administrator, not a salesperson.")
    existing = EggPOSAdminLoanTransaction.objects.filter(request_token=token).first()
    if existing:
        return existing, True
    outstanding = loan_outstanding(user.pk)
    if kind == "repayment" and amount > outstanding:
        raise ValueError(f"Repayment exceeds this administrator's outstanding loan: Rs {outstanding:,.2f}.")
    if kind == "advance":
        # Lock account for simultaneous payments within this module; other cash-writing
        # modules should also follow the cash-balance lock convention.
        from api.models.accounting import ChartOfAccount
        from api.services.accounting import ensure_default_accounts
        ensure_default_accounts()
        ChartOfAccount.objects.select_for_update().get(code=SOURCE_ACCOUNT[payment_source])
        available = normal_account_balance(SOURCE_ACCOUNT[payment_source])
        if amount > available:
            raise ValueError(f"Insufficient {payment_source} funds: available Rs {available:,.2f}.")
    row = EggPOSAdminLoanTransaction.objects.create(
        administrator=user, kind=kind, amount=amount, transaction_date=transaction_date,
        payment_source=payment_source, reference=reference.strip()[:120], notes=notes.strip()[:500],
        request_token=token, recorded_by=recorded_by,
    )
    sync_admin_loan(row)
    return row, False


def rebuild_admin_loans():
    for row in EggPOSAdminLoanTransaction.objects.select_related('administrator', 'recorded_by').order_by('pk'):
        sync_admin_loan(row)
