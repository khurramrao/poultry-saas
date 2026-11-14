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
    # Admin-reviewed part of historically paid, overlapping commission treated
    # as salary already paid in the first salary month. No commission entry deleted.
    commission_credit_amount = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    commission_credit_reason = models.CharField(max_length=500, blank=True)
    commission_overlap_ids = models.JSONField(default=list, blank=True)
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
        if self.commission_credit_amount < ZERO or (self.monthly_salary and self.commission_credit_amount > self.monthly_salary):
            raise ValidationError({'commission_credit_amount': 'Commission credit must be between zero and one monthly salary.'})


class EggPOSPayrollPeriod(models.Model):
    employee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='egg_pos_payroll_periods')
    month = models.DateField()  # First of month
    salary_amount = models.DecimalField(max_digits=14, decimal_places=2)
    commission_credit_amount = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
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
        return max(ZERO, self.salary_amount - self.commission_credit_amount - self.amount_paid - self.advances_applied)


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


class EggPOSAdminVariablePayment(models.Model):
    """One-off payment to a named administrator; no fixed salary schedule.

    salary: compensation for services (profit and loss).
    drawing: owner equity withdrawal (balance sheet, not expense).
    A superuser account alone does not establish ownership; the recorder must
    choose the correct classification for each payment.
    """
    KIND_CHOICES = [
        ('salary', 'Variable remuneration / wages (expense)'),
        ('drawing', 'Owner drawing (equity, not salary expense)'),
    ]
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                  related_name='egg_pos_admin_variable_payments')
    paid_on = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    kind = models.CharField(max_length=12, choices=KIND_CHOICES)
    payment_source = models.CharField(max_length=10, choices=PAYMENT_CHOICES)
    reference = models.CharField(max_length=120, blank=True)
    notes = models.CharField(max_length=500, blank=True)
    request_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                    related_name='egg_pos_admin_variable_payments_recorded')
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-paid_on', '-id']

    def clean(self):
        if self.recipient_id and not self.recipient.is_superuser:
            raise ValidationError({'recipient': 'Choose an authorized administrator.'})
        if self.amount is None or self.amount <= ZERO:
            raise ValidationError({'amount': 'Payment must be greater than zero.'})
        if self.kind not in dict(self.KIND_CHOICES):
            raise ValidationError({'kind': 'Choose a payment classification.'})


class EggPOSSalaryAdvanceCorrection(models.Model):
    """Append-only audit of genuine salary-advance amount corrections."""
    advance = models.ForeignKey(
        EggPOSSalaryAdvance, on_delete=models.PROTECT, related_name='corrections'
    )
    old_amount = models.DecimalField(max_digits=14, decimal_places=2)
    new_amount = models.DecimalField(max_digits=14, decimal_places=2)
    reason = models.CharField(max_length=500)
    corrected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='egg_pos_salary_advance_corrections'
    )
    corrected_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-corrected_at', '-id']
