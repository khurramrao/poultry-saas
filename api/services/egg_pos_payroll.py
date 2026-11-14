"""Double-entry staff salary ledger and oldest-first advance settlement."""
from datetime import date, timedelta
from calendar import monthrange
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from api.models.egg_pos import EggPOSCommissionPeriod
from api.models.egg_pos_payroll import (
    EggPOSSalaryPlan, EggPOSPayrollPeriod, EggPOSSalaryAdvance,
    EggPOSSalaryAdvanceAllocation, EggPOSSalaryPayment, EggPOSSalaryAdvanceCorrection, EggPOSAdminVariablePayment,
)
from api.services.accounting import line, post_entry

ZERO = Decimal('0.00')
PAYMENT_ACCOUNT = {'cash': '1000', 'bank': '1010'}


def current_plan(employee_id, on_month):
    return (EggPOSSalaryPlan.objects.filter(employee_id=employee_id, effective_month__lte=on_month)
            .order_by('-effective_month', '-id').first())


def salary_cutoff(employee_id):
    """Beginning of fixed-salary compensation. Prior commissions are retained."""
    return (EggPOSSalaryPlan.objects.filter(employee_id=employee_id)
            .order_by('effective_month').values_list('effective_month', flat=True).first())


def _staff_line(code, *, debit=ZERO, credit=ZERO, user_id, description):
    return line(code, debit=debit, credit=credit, description=description,
                party_type='user', party_id=user_id)


def sync_salary_period(payroll):
    salary_end = date(payroll.month.year, payroll.month.month, monthrange(payroll.month.year, payroll.month.month)[1])
    rows = [
        _staff_line('6390', debit=payroll.salary_amount, user_id=payroll.employee_id, description='Salary expense'),
        _staff_line('2050', credit=payroll.salary_amount, user_id=payroll.employee_id, description='Salary payable'),
    ]
    # A previously paid, overlapping commission is a credit against first-month
    # salary, not new cash. Reverse just its agreed commission expense portion
    # and reduce salary payable. Never change original commission/payment records.
    if payroll.commission_credit_amount > ZERO:
        rows += [
            _staff_line('2050', debit=payroll.commission_credit_amount,
                        user_id=payroll.employee_id, description='Historic paid commission credited against salary payable'),
            _staff_line('6200', credit=payroll.commission_credit_amount,
                        user_id=payroll.employee_id, description='Reclassify paid overlapping commission into salary'),
        ]
    return post_entry(
        source_key=f'egg_pos:salary_period:{payroll.pk}',
        entry_date=salary_end,
        reference=f'SALARY-{payroll.month:%Y%m}-{payroll.employee_id}',
        memo=f'Staff salary earned for {payroll.month:%b %Y}: {payroll.employee.username}',
        source_type='egg_pos_salary_period', source_id=payroll.pk, created_by=payroll.posted_by,
        lines=rows,
    )


def verify_commission_transition(employee_id, month, salary_amount, credit_amount):
    """Return immutable overlap evidence; never infer which days earned the amount.

    Crediting is only permitted for FULLY SETTLED commission periods. It requires
    an administrator to specify the supported November amount, not prorate a
    spanning period (which could contain loss carry or uneven daily profit).
    """
    cutoff = month.replace(day=1)
    period_end = date(month.year, month.month, monthrange(month.year, month.month)[1])
    overlapping = list(EggPOSCommissionPeriod.objects.filter(
        salesperson_id=employee_id, period_end__gte=cutoff,
    ).prefetch_related('payments', 'advance_allocations').order_by('period_start', 'pk'))
    if overlapping:
        if any(p.period_start > period_end or p.period_end > period_end for p in overlapping):
            raise ValueError('Commission overlaps later salary months. Reconcile that period separately before switching.')
        if any(p.balance_due > ZERO for p in overlapping):
            raise ValueError('Overlapping commission is not fully paid. Reconcile its payable before starting salary.')
        total = sum((p.commission_amount for p in overlapping), ZERO)
        if total > ZERO and credit_amount <= ZERO:
            raise ValueError('Enter the already-paid November commission amount credited to the new salary. Do not leave it blank.')
        if credit_amount > total:
            raise ValueError(f'Commission credit exceeds posted overlapping commission of Rs {total:,.2f}.')
        if credit_amount > salary_amount:
            raise ValueError('Commission credit cannot exceed the agreed monthly salary.')
    else:
        if credit_amount != ZERO:
            raise ValueError('No posted commission overlaps this salary month; leave its credit at zero.')
        latest = (EggPOSCommissionPeriod.objects.filter(salesperson_id=employee_id)
                  .order_by('-period_end', '-pk').first())
        if latest is not None and latest.period_end != cutoff - timedelta(days=1):
            raise ValueError(f'Finalize previous commission through {(cutoff - timedelta(days=1)):%d %b %Y} first. Unpaid old balances can remain payable.')
    return overlapping


def sync_salary_advance(advance):
    return post_entry(
        source_key=f'egg_pos:salary_advance:{advance.pk}',
        entry_date=advance.paid_on,
        reference=advance.reference or f'SALADV-{advance.pk:06d}',
        memo=f'Salary advance: {advance.employee.username}',
        source_type='egg_pos_salary_advance', source_id=advance.pk, created_by=advance.recorded_by,
        lines=[
            _staff_line('1310', debit=advance.amount, user_id=advance.employee_id, description='Salary advance receivable'),
            _staff_line(PAYMENT_ACCOUNT[advance.payment_source], credit=advance.amount,
                        user_id=advance.employee_id, description='Cash/bank paid for salary advance'),
        ],
    )


def sync_salary_allocation(allocation):
    month = allocation.payroll.month
    period_end = date(month.year, month.month, monthrange(month.year, month.month)[1])
    applied_on = max(period_end, allocation.advance.paid_on)
    return post_entry(
        source_key=f'egg_pos:salary_allocation:{allocation.pk}',
        entry_date=applied_on,
        reference=f'SALALLOC-{allocation.pk:06d}',
        memo=f'Apply salary advance against {allocation.payroll.month:%b %Y}',
        source_type='egg_pos_salary_advance_allocation', source_id=allocation.pk,
        created_by=allocation.payroll.posted_by,
        lines=[
            _staff_line('2050', debit=allocation.amount, user_id=allocation.payroll.employee_id, description='Reduce salary payable'),
            _staff_line('1310', credit=allocation.amount, user_id=allocation.payroll.employee_id, description='Apply existing salary advance'),
        ],
    )


def sync_salary_payment(payment):
    return post_entry(
        source_key=f'egg_pos:salary_payment:{payment.pk}', entry_date=payment.paid_on,
        reference=payment.reference or f'SALPAY-{payment.pk:06d}',
        memo=f'Salary payment {payment.payroll.month:%b %Y}: {payment.payroll.employee.username}',
        source_type='egg_pos_salary_payment', source_id=payment.pk, created_by=payment.recorded_by,
        lines=[
            _staff_line('2050', debit=payment.amount, user_id=payment.payroll.employee_id, description='Settle salary payable'),
            _staff_line(PAYMENT_ACCOUNT[payment.payment_source], credit=payment.amount,
                        user_id=payment.payroll.employee_id, description='Salary paid from company funds'),
        ],
    )


@transaction.atomic
def post_salary(employee_id, month, posted_by):
    if month.day != 1:
        raise ValueError('Salary month must start on the first day.')
    # Payroll becomes due after month ends; mid-month cash is a salary advance.
    now = timezone.localdate()
    if month >= now.replace(day=1):
        raise ValueError('Post monthly salary after the month ends. Use Salary Advance for earlier cash payments.')
    get_user_model().objects.select_for_update().get(pk=employee_id)
    if EggPOSPayrollPeriod.objects.filter(employee_id=employee_id, month=month).exists():
        raise ValueError('This month has already been posted; no duplicate salary will be created.')
    plan = current_plan(employee_id, month)
    if not plan:
        raise ValueError('Configure a salary plan effective on or before this month.')
    original_start = salary_cutoff(employee_id)
    credit = plan.commission_credit_amount if month == original_start and month == plan.effective_month else ZERO
    payroll = EggPOSPayrollPeriod.objects.create(employee_id=employee_id, month=month,
                                                  salary_amount=plan.monthly_salary,
                                                  commission_credit_amount=credit, posted_by=posted_by)
    sync_salary_period(payroll)
    apply_salary_advances_locked(employee_id)
    return payroll


def apply_salary_advances_locked(employee_id):
    """Call inside transaction after row-locking the employee: oldest payroll, oldest advance."""
    periods = list(EggPOSPayrollPeriod.objects.filter(employee_id=employee_id).order_by('month', 'id'))
    advances = list(EggPOSSalaryAdvance.objects.filter(employee_id=employee_id).order_by('paid_on', 'id'))
    total = ZERO
    for period in periods:
        due = period.balance_due
        if due <= ZERO:
            continue
        for advance in advances:
            if due <= ZERO:
                break
            available = advance.available
            if available <= ZERO:
                continue
            applied = min(due, available)
            alloc = EggPOSSalaryAdvanceAllocation.objects.create(
                advance=advance, payroll=period, amount=applied,
            )
            sync_salary_allocation(alloc)
            due -= applied
            total += applied
    return total


@transaction.atomic
def record_salary_advance(employee_id, amount, paid_on, payment_source, recorded_by, request_token, reference='', notes=''):
    from uuid import UUID
    token = UUID(str(request_token))
    get_user_model().objects.select_for_update().get(pk=employee_id)
    existing = EggPOSSalaryAdvance.objects.filter(request_token=token).first()
    if existing:
        return existing, True
    cutoff = salary_cutoff(employee_id)
    if not cutoff:
        raise ValueError('Set a salary plan before paying a salary advance.')
    if paid_on < cutoff:
        raise ValueError('Salary advance date must be on or after salary start date.')
    if amount <= ZERO or paid_on > timezone.localdate() or payment_source not in PAYMENT_ACCOUNT:
        raise ValueError('Invalid amount, date or payment source.')
    advance = EggPOSSalaryAdvance.objects.create(
        employee_id=employee_id, paid_on=paid_on, amount=amount,
        payment_source=payment_source, reference=reference[:120], notes=notes[:250],
        recorded_by=recorded_by, request_token=token,
    )
    sync_salary_advance(advance)
    apply_salary_advances_locked(employee_id)
    return advance, False


@transaction.atomic
def record_salary_payment(payroll_id, amount, paid_on, payment_source, recorded_by, request_token, reference=''):
    from uuid import UUID
    token = UUID(str(request_token))
    existing = EggPOSSalaryPayment.objects.filter(request_token=token).first()
    if existing:
        return existing, True
    # Lock employee so advance/pay processes cannot interleave.
    payroll = EggPOSPayrollPeriod.objects.get(pk=payroll_id)
    get_user_model().objects.select_for_update().get(pk=payroll.employee_id)
    payroll = EggPOSPayrollPeriod.objects.get(pk=payroll_id)
    end_of_month = date(payroll.month.year, payroll.month.month, monthrange(payroll.month.year, payroll.month.month)[1])
    if amount <= ZERO or paid_on > timezone.localdate() or paid_on < end_of_month or payment_source not in PAYMENT_ACCOUNT:
        raise ValueError('Invalid amount, date or payment source. Use Salary Advance for payments before month-end.')
    if amount > payroll.balance_due:
        raise ValueError(f'Payment exceeds outstanding salary Rs {payroll.balance_due:,.2f}.')
    payment = EggPOSSalaryPayment.objects.create(
        payroll=payroll, paid_on=paid_on, amount=amount,
        payment_source=payment_source, reference=reference[:120],
        recorded_by=recorded_by, request_token=token,
    )
    sync_salary_payment(payment)
    return payment, False


def sync_admin_variable_payment(payment):
    """Rebuildable financial record with individual recipient attribution."""
    debit_code = '6390' if payment.kind == 'salary' else '3000'
    description = ('Variable admin remuneration' if payment.kind == 'salary' else 'Owner withdrawal by named admin')
    return post_entry(
        source_key=f'egg_pos:admin_variable_payment:{payment.pk}',
        entry_date=payment.paid_on,
        reference=payment.reference or f'ADMPAY-{payment.pk:06d}',
        memo=f'{description}: {payment.recipient.username}',
        source_type='egg_pos_admin_variable_payment', source_id=payment.pk,
        created_by=payment.recorded_by,
        lines=[
            _staff_line(debit_code, debit=payment.amount,
                        user_id=payment.recipient_id, description=description),
            _staff_line(PAYMENT_ACCOUNT[payment.payment_source], credit=payment.amount,
                        user_id=payment.recipient_id, description='Payment to named administrator'),
        ],
    )


@transaction.atomic
def record_admin_variable_payment(*, recipient_id, amount, paid_on, kind,
                                  payment_source, recorded_by, request_token,
                                  reference='', notes=''):
    from uuid import UUID
    from api.models.accounting import ChartOfAccount
    from api.services.accounting import ensure_default_accounts, normal_account_balance

    try:
        token = UUID(str(request_token))
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Form token invalid. Reload the page and try again.')
    prior = EggPOSAdminVariablePayment.objects.filter(request_token=token).first()
    if prior:
        return prior, True
    if not recorded_by.is_superuser:
        raise ValueError('Only a superuser may record administrator payouts.')
    recipient = get_user_model().objects.select_for_update().get(pk=recipient_id)
    if not recipient.is_active or not recipient.is_superuser:
        raise ValueError('Select an active administrator for this payout.')
    if kind not in ('salary', 'drawing'):
        raise ValueError('Choose remuneration or owner drawing.')
    if payment_source not in PAYMENT_ACCOUNT:
        raise ValueError('Choose Main Cash or Bank / Digital.')
    amount = Decimal(str(amount)).quantize(Decimal('0.01'))
    if not amount.is_finite() or amount <= ZERO or amount > Decimal('999999999999.99'):
        raise ValueError('Enter a positive payment amount.')
    if paid_on > timezone.localdate():
        raise ValueError('Future-dated payments are not allowed.')

    ensure_default_accounts()
    # Use the same ledger account lock used by Transfer Funds to avoid
    # accepting two simultaneous payments against the same funds.
    source_code = PAYMENT_ACCOUNT[payment_source]
    list(ChartOfAccount.objects.select_for_update().filter(code=source_code))
    available = normal_account_balance(source_code)
    if amount > available:
        raise ValueError(f'Insufficient funds: only Rs {available:,.2f} available in this account.')
    if kind == 'drawing' and not (notes or '').strip():
        raise ValueError('Enter a note identifying the owner for an owner drawing.')
    payment = EggPOSAdminVariablePayment.objects.create(
        recipient=recipient, amount=amount, paid_on=paid_on, kind=kind,
        payment_source=payment_source, reference=(reference or '')[:120],
        notes=(notes or '')[:500], request_token=token, recorded_by=recorded_by,
    )
    sync_admin_variable_payment(payment)
    return payment, False


def rebuild_staff_payroll():
    for payment in EggPOSAdminVariablePayment.objects.select_related('recipient', 'recorded_by').all():
        sync_admin_variable_payment(payment)
    for payroll in EggPOSPayrollPeriod.objects.select_related('employee', 'posted_by').all():
        sync_salary_period(payroll)
    for advance in EggPOSSalaryAdvance.objects.select_related('employee', 'recorded_by').all():
        sync_salary_advance(advance)
    for allocation in EggPOSSalaryAdvanceAllocation.objects.select_related('payroll', 'payroll__employee', 'payroll__posted_by').all():
        sync_salary_allocation(allocation)
    for payment in EggPOSSalaryPayment.objects.select_related('payroll', 'payroll__employee', 'recorded_by').all():
        sync_salary_payment(payment)


@transaction.atomic
def correct_unapplied_salary_advance(advance_id, new_amount, reason, corrected_by):
    """Correct a genuine overstatement; never create a second cash outflow.

    Keep the original advance identity/date/source. The accounting journal is
    regenerated using the corrected amount, and correction history stays
    immutable. Allocated advances are blocked to avoid changing prior payroll.
    """
    if not corrected_by.is_superuser:
        raise ValueError('Only a superuser can correct salary advances.')
    employee_id = EggPOSSalaryAdvance.objects.values_list('employee_id', flat=True).get(pk=advance_id)
    get_user_model().objects.select_for_update().get(pk=employee_id)
    advance = (EggPOSSalaryAdvance.objects.select_for_update()
               .select_related('employee').get(pk=advance_id))
    new_amount = Decimal(new_amount).quantize(Decimal('0.01'))
    old_amount = advance.amount
    if not new_amount.is_finite() or new_amount <= ZERO:
        raise ValueError('Corrected advance must be positive.')
    if new_amount >= old_amount:
        raise ValueError('This correction form only reduces an overstated advance. Contact accounting for an understatement.')
    if advance.allocations.exists():
        raise ValueError('This advance is already applied to posted salary. Reconcile that allocation before correcting the original payment.')
    reason = (reason or '').strip()
    if len(reason) < 12:
        raise ValueError('Explain the correction in at least 12 characters.')
    advance.amount = new_amount
    advance.save(update_fields=['amount'])
    # This is a replacement of the original journal source key, not an extra
    # payment. Corrects cash/bank and Salary Advance Receivable equally.
    sync_salary_advance(advance)
    return EggPOSSalaryAdvanceCorrection.objects.create(
        advance=advance, old_amount=old_amount, new_amount=new_amount,
        reason=reason[:500], corrected_by=corrected_by
    )
