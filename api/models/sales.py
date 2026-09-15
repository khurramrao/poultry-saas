from django.db import models
from django.conf import settings
from api.models.sensor import Batch
from django.utils import timezone
from datetime import date
from decimal import Decimal



class ChickCostEntry(models.Model):
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE)
    entry_date = models.DateField()

    chick_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    carriage_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def total_amount(self):
        return self.chick_cost + self.carriage_cost

    def __str__(self):
        return f"Batch {self.batch.batch_number} Chick + Carriage"


class SaleRecord(models.Model):
    SALE_MODE_CHOICES = [
        ("counted", "Counted Birds"),
        ("weight_only", "Weight Only / Bird Count Unknown"),
    ]

    batch = models.ForeignKey(Batch, on_delete=models.CASCADE)
    sale_date = models.DateField(default=date.today)

    sale_mode = models.CharField(
        max_length=20,
        choices=SALE_MODE_CHOICES,
        default="counted",
    )
    birds_sold = models.PositiveIntegerField(null=True, blank=True)
    cogs_locked = models.BooleanField(default=True)
    total_weight_kg = models.DecimalField(max_digits=10, decimal_places=2)
    rate_per_kg = models.DecimalField(max_digits=10, decimal_places=2)

    cogs_per_bird_at_sale = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0
    )

    cogs_allocated = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0
    )

    gross_profit = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0
    )

    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    discount_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0
    )

    @property
    def average_weight_kg(self):
        if self.birds_sold:
            return (
                    self.total_weight_kg / Decimal(self.birds_sold)
            ).quantize(Decimal("0.001"))
        return Decimal("0.000")

    @property
    def gross_amount(self):
        return (
                self.total_weight_kg * self.rate_per_kg
        ).quantize(Decimal("0.01"))

    @property
    def total_amount(self):
        return (
                self.gross_amount - self.discount_amount
        ).quantize(Decimal("0.01"))


class BatchBirdSaleReconciliation(models.Model):
    """Admin confirmation that no physical birds remain in the batch.

    This does not rewrite the bird count on individual weight-only sales.
    Instead it records the otherwise-unknown quantity at batch level and lets
    finance realize all remaining COGS while the batch can stay Active for
    review before final closure.
    """

    batch = models.OneToOneField(
        Batch,
        on_delete=models.CASCADE,
        related_name="bird_sale_reconciliation",
    )
    reconciliation_date = models.DateField(default=timezone.localdate)
    counted_birds_sold = models.PositiveIntegerField(default=0)
    reconciled_weight_only_birds = models.PositiveIntegerField(default=0)
    total_birds_sold = models.PositiveIntegerField(default=0)
    total_weight_sold_kg = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0.00")
    )
    total_sales_revenue = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0.00")
    )
    total_cogs_snapshot = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0.00")
    )
    remaining_cogs_realized = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0.00")
    )
    notes = models.TextField(blank=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="confirmed_all_birds_sold_reconciliations",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reversed_all_birds_sold_reconciliations",
    )

    class Meta:
        verbose_name = "Batch Bird Sale Reconciliation"
        verbose_name_plural = "Batch Bird Sale Reconciliations"

    def __str__(self):
        state = "Active" if self.is_active else "Reversed"
        return f"Batch {self.batch.batch_number} - All Birds Sold ({state})"



class Expense(models.Model):
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE)

    expense_date = models.DateField(default=timezone.now)

    category = models.CharField(
        max_length=50,
        choices=[
            ("fuel", "Fuel"),
            ("labor", "Labor"),
            ("electricity", "Electricity"),
            ("transport", "Transport"),
            ("maintenance", "Maintenance"),
            ("rent", "Farm Rent"),
            ("internet", "Internet"),
            ("service", "Service Charges"),
            ("misc", "Misc"),
        ]
    )

    description = models.CharField(max_length=255, blank=True)

    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2
    )

    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.batch.batch_number} - {self.category} - Rs {self.amount}"