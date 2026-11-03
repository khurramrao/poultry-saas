"""Admin-only payroll. Existing commission payments and periods are read-only here."""
import uuid
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from api.models.egg_pos import EggPOSCommissionPeriod
from api.models.egg_pos_payroll import (
    EggPOSSalaryPlan, EggPOSPayrollPeriod, EggPOSSalaryAdvance,
    EggPOSSalaryPayment, PAYMENT_CHOICES,
)
from api.services.egg_pos import can_manage, can_sell
from api.services.egg_pos_payroll import (
    post_salary, record_salary_advance, record_salary_payment, salary_cutoff,
)


def _amount(raw):
    try:
        val = Decimal(str(raw)).quantize(Decimal('0.01'))
    except (ValueError, InvalidOperation, TypeError):
        raise ValueError('Enter a valid amount.')
    if not val.is_finite() or val <= 0 or val > Decimal('999999999999.99'):
        raise ValueError('Enter a positive amount within the allowed limit.')
    return val


def _date(raw, label):
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError):
        raise ValueError(f'Enter a valid {label}.')


def _admin(request):
    return request.user.is_authenticated and (request.user.is_superuser or (request.user.is_staff and can_manage(request.user)))


@login_required
@require_http_methods(['GET', 'POST'])
def payroll(request):
    if not _admin(request):
        return HttpResponseForbidden('Only authorized Egg POS administrators may access payroll.')
    today = timezone.localdate()
    User = get_user_model()
    staff = [user for user in User.objects.filter(is_active=True).order_by('username') if can_sell(user)]

    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()
        try:
            if not request.user.has_usable_password() or not request.user.check_password(request.POST.get('admin_password') or ''):
                raise ValueError('Admin password is required for every payroll action.')
            if action == 'plan':
                employee = get_object_or_404(User, pk=request.POST.get('employee'), is_active=True)
                month = _date(request.POST.get('effective_month'), 'salary start month')
                salary = _amount(request.POST.get('monthly_salary'))
                if month.day != 1:
                    raise ValueError('Salary start must be the first day of a month.')
                if month > today.replace(day=1):
                    raise ValueError('Future salaries can be set up in their start month. No future-dated switch here.')
                with transaction.atomic():
                    User.objects.select_for_update().get(pk=employee.pk)
                    if EggPOSSalaryPlan.objects.filter(employee=employee, effective_month=month).exists():
                        raise ValueError('A rate is already configured for that month; history cannot be overwritten.')
                    if EggPOSPayrollPeriod.objects.filter(employee=employee, month__gte=month).exists():
                        raise ValueError('Cannot backdate a salary rate across posted payroll months.')
                    prior = salary_cutoff(employee.pk)
                    if prior is None:
                        # Prevent cutting off unpaid or unposted October commission days.
                        overlap = EggPOSCommissionPeriod.objects.filter(
                            salesperson=employee, period_end__gte=month
                        ).exists()
                        if overlap:
                            raise ValueError('Posted commission overlaps the salary start date. Reconcile the earlier periods first.')
                        latest = EggPOSCommissionPeriod.objects.filter(
                            salesperson=employee
                        ).order_by('-period_end', '-pk').first()
                        if latest is not None and latest.period_end != month - timedelta(days=1):
                            raise ValueError(
                                f'Finalize prior commission through {(month - timedelta(days=1)):%d %b %Y} '
                                'before switching to monthly salary. Existing commission will remain payable.'
                            )
                    elif month <= prior:
                        raise ValueError('A salary revision must start after the original salary start month.')
                    EggPOSSalaryPlan.objects.create(
                        employee=employee, effective_month=month, monthly_salary=salary,
                        notes=(request.POST.get('notes') or '').strip()[:250], created_by=request.user,
                    )
                messages.success(request, f'Salary plan saved for {employee.username} from {month:%b %Y}. Existing commission history remains unchanged.')
            elif action == 'post':
                employee = get_object_or_404(User, pk=request.POST.get('employee'), is_active=True)
                month = _date(request.POST.get('month'), 'payroll month')
                period = post_salary(employee.pk, month, request.user)
                messages.success(request, f'{period.month:%b %Y} salary Rs {period.salary_amount:,.2f} posted for {employee.username}.')
            elif action == 'advance':
                employee = get_object_or_404(User, pk=request.POST.get('employee'), is_active=True)
                adv, repeated = record_salary_advance(
                    employee.pk, _amount(request.POST.get('amount')),
                    _date(request.POST.get('paid_on'), 'advance date'),
                    request.POST.get('payment_source'), request.user,
                    request.POST.get('request_token'),
                    reference=(request.POST.get('reference') or '').strip(),
                    notes=(request.POST.get('notes') or '').strip(),
                )
                messages.info(request, 'Duplicate submission ignored; no second cash payment.') if repeated else messages.success(
                    request, f'Salary advance Rs {adv.amount:,.2f} recorded for {employee.username}.'
                )
            elif action == 'pay':
                period = get_object_or_404(EggPOSPayrollPeriod, pk=request.POST.get('payroll_id'))
                payment, repeated = record_salary_payment(
                    period.pk, _amount(request.POST.get('amount')),
                    _date(request.POST.get('paid_on'), 'payment date'),
                    request.POST.get('payment_source'), request.user,
                    request.POST.get('request_token'),
                    reference=(request.POST.get('reference') or '').strip(),
                )
                messages.info(request, 'Duplicate submission ignored; no second cash payment.') if repeated else messages.success(
                    request, f'Paid Rs {payment.amount:,.2f} toward {period.month:%b %Y} salary.'
                )
            else:
                raise ValueError('Invalid payroll action.')
            return redirect('egg_pos_payroll')
        except Exception as exc:
            messages.error(request, str(exc))

    plans = list(EggPOSSalaryPlan.objects.select_related('employee', 'created_by').order_by('employee__username', '-effective_month')[:150])
    periods = list(EggPOSPayrollPeriod.objects.select_related('employee', 'posted_by').prefetch_related('payments', 'advance_allocations').order_by('-month', 'employee__username')[:150])
    advances = list(EggPOSSalaryAdvance.objects.select_related('employee').prefetch_related('allocations').order_by('-paid_on', '-id')[:100])
    payments = list(EggPOSSalaryPayment.objects.select_related('payroll', 'payroll__employee').order_by('-paid_on', '-id')[:100])
    return render(request, 'api/egg_pos_payroll.html', {
        'staff': staff, 'plans': plans, 'periods': periods, 'advances': advances, 'payments': payments,
        'payment_sources': PAYMENT_CHOICES,
        'today': today, 'current_month': today.replace(day=1),
        'last_month': (today.replace(day=1) - timedelta(days=1)).replace(day=1),
        'request_token_advance': str(uuid.uuid4()),
        'request_token_pay': str(uuid.uuid4()),
    })
