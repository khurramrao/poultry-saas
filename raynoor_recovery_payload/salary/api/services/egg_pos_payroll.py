"""Double-entry staff salary ledger and oldest-first advance settlement."""
from datetime import date
from calendar import monthrange
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from api.models.egg_pos_payroll import (
    EggPOSSalaryPlan, EggPOSPayrollPeriod, EggPOSSalaryAdvance,
    EggPOSSalaryAdvanceAllocation, EggPOSSalaryPayment,
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
    return post_entry(
        source_key=f'egg_pos:salary_period:{payroll.pk}',
        entry_date=salary_end,
        reference=f'SALARY-{payroll.month:%Y%m}-{payroll.employee_id}',
        memo=f'Staff salary earned for {payroll.month:%b %Y}: {payroll.employee.username}',
        source_type='egg_pos_salary_period', source_id=payroll.pk, created_by=payroll.posted_by,
        lines=[
            _staff_line('6390', debit=payroll.salary_amount, user_id=payroll.employee_id, description='Salary expense'),
            _staff_line('2050', credit=payroll.salary_amount, user_id=payroll.employee_id, description='Salary payable'),
        ],
    )


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
    payroll = EggPOSPayrollPeriod.objects.create(employee_id=employee_id, month=month,
                                                  salary_amount=plan.monthly_salary, posted_by=posted_by)
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


def rebuild_staff_payroll():
    for payroll in EggPOSPayrollPeriod.objects.select_related('employee', 'posted_by').all():
        sync_salary_period(payroll)
    for advance in EggPOSSalaryAdvance.objects.select_related('employee', 'recorded_by').all():
        sync_salary_advance(advance)
    for allocation in EggPOSSalaryAdvanceAllocation.objects.select_related('payroll', 'payroll__employee', 'payroll__posted_by').all():
        sync_salary_allocation(allocation)
    for payment in EggPOSSalaryPayment.objects.select_related('payroll', 'payroll__employee', 'recorded_by').all():
        sync_salary_payment(payment)
