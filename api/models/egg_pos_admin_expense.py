"""Admin-only general operating costs for the Egg POS business, not selling commission costs."""
import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class EggPOSAdminExpense(models.Model):
    CATEGORY_CHOICES = [
        ("rent", "Office / Shop Rent"),
        ("utilities", "Electricity / Utilities"),
        ("internet", "Internet / Phone"),
        ("stationery", "Stationery / Printing"),
        ("supplies", "Office Consumables"),
        ("repair", "Repairs & Maintenance"),
        ("marketing", "Marketing / Advertising"),
        ("delivery", "Customer Delivery / Courier Charges"),
        ("professional", "Professional / Admin Fees"),
        ("equipment", "Equipment (Fixed Asset)"),
        ("other", "Other General Expense"),
    ]
    PAYMENT_CHOICES = [
        ("cash", "Main Cash"),
        ("bank", "Bank / Digital Wallet"),
    ]
    expense_date = models.DateField(default=timezone.localdate)
    category = models.CharField(max_length=24, choices=CATEGORY_CHOICES)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    payment_source = models.CharField(max_length=10, choices=PAYMENT_CHOICES)
    paid_to = models.CharField(max_length=160, blank=True)
    reference = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    request_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="egg_pos_admin_expenses",
        on_delete=models.PROTECT,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    is_voided = models.BooleanField(default=False)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        related_name="egg_pos_admin_expenses_voided", on_delete=models.SET_NULL,
    )
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-expense_date", "-id"]
        indexes = [models.Index(fields=["expense_date", "is_voided"], name="ep_adminexp_date_idx")]

    @property
    def is_capital_asset(self):
        return self.category == "equipment"

    def clean(self):
        super().clean()
        if self.amount is None or Decimal(self.amount) <= Decimal("0.00"):
            raise ValidationError({"amount": "Amount must be greater than zero."})

    def __str__(self):
        return f"ADMINEXP-{self.pk}: {self.get_category_display()} Rs {self.amount}"
