"""Egg POS payroll. No commission amounts or customer cash collections are changed."""
import uuid
from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

ZERO = Decimal('0.00')
PAYMENT_CHOICES = [('cash', 'Main Cash'), ('bank', 'Bank / Digital Wallet')]


class EggPOSSalaryPlan(models.Model):
    """Append-only staff pay rate effective on the first day of a month."""
    employee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='egg_pos_salary_plans')
    effective_month = models.DateField()
    monthly_salary = models.DecimalField(max_digits=14, decimal_places=2)
    notes = models.CharField(max_length=250, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='egg_pos_salary_plans_created')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['employee_id', '-effective_month', '-id']
        constraints = [models.UniqueConstraint(fields=['employee', 'effective_month'], name='ep_salaryplan_employee_month_uniq')]

    def clean(self):
        if self.effective_month and self.effective_month.day != 1:
            raise ValidationError({'effective_month': 'Use the first day of the salary month.'})
        if self.monthly_salary is None or self.monthly_salary <= ZERO:
            raise ValidationError({'monthly_salary': 'Monthly salary must be greater than zero.'})


class EggPOSPayrollPeriod(models.Model):
    employee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='egg_pos_payroll_periods')
    month = models.DateField()  # First of month
    salary_amount = models.DecimalField(max_digits=14, decimal_places=2)
    posted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='egg_pos_payroll_posted')
    posted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-month', '-id']
        constraints = [models.UniqueConstraint(fields=['employee', 'month'], name='ep_payroll_employee_month_uniq')]

    @property
    def amount_paid(self):
        return sum((p.amount for p in self.payments.all()), ZERO)

    @property
    def advances_applied(self):
        return sum((a.amount for a in self.advance_allocations.all()), ZERO)

    @property
    def balance_due(self):
        return max(ZERO, self.salary_amount - self.amount_paid - self.advances_applied)


class EggPOSSalaryAdvance(models.Model):
    employee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='egg_pos_salary_advances')
    paid_on = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    payment_source = models.CharField(max_length=10, choices=PAYMENT_CHOICES)
    reference = models.CharField(max_length=120, blank=True)
    notes = models.CharField(max_length=250, blank=True)
    request_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='egg_pos_salary_advances_recorded')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['paid_on', 'id']

    @property
    def available(self):
        return max(ZERO, self.amount - sum((a.amount for a in self.allocations.all()), ZERO))


class EggPOSSalaryAdvanceAllocation(models.Model):
    advance = models.ForeignKey(EggPOSSalaryAdvance, on_delete=models.PROTECT, related_name='allocations')
    payroll = models.ForeignKey(EggPOSPayrollPeriod, on_delete=models.PROTECT, related_name='advance_allocations')
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    allocated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['payroll__month', 'id']
        constraints = [models.UniqueConstraint(fields=['advance', 'payroll'], name='ep_salaryadvance_payroll_uniq')]


class EggPOSSalaryPayment(models.Model):
    payroll = models.ForeignKey(EggPOSPayrollPeriod, on_delete=models.PROTECT, related_name='payments')
    paid_on = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    payment_source = models.CharField(max_length=10, choices=PAYMENT_CHOICES)
    reference = models.CharField(max_length=120, blank=True)
    request_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='egg_pos_salary_payments_recorded')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-paid_on', '-id']
