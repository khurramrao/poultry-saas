"""Company advances to an authorised POS administrator, and their repayments.

This is a receivable (asset); it is not a payroll advance or business expense.
"""
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone


class EggPOSAdminLoanTransaction(models.Model):
    KIND_CHOICES = [("advance", "Paid to administrator"), ("repayment", "Repaid by administrator")]
    PAYMENT_CHOICES = [("cash", "Main Cash"), ("bank", "Bank / Digital Wallet")]
    administrator = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="raynoor_admin_loans_received",
    )
    kind = models.CharField(max_length=12, choices=KIND_CHOICES)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    transaction_date = models.DateField(default=timezone.localdate)
    payment_source = models.CharField(max_length=10, choices=PAYMENT_CHOICES)
    reference = models.CharField(max_length=120, blank=True)
    notes = models.CharField(max_length=500, blank=True)
    request_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="raynoor_admin_loans_recorded",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-transaction_date", "-pk"]
        indexes = [models.Index(fields=["administrator", "transaction_date"], name="ep_adminloan_date_idx")]

    def __str__(self):
        return f"{self.administrator.username}: {self.kind} Rs {self.amount}"
