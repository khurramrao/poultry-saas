from django.db import models
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
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE)
    sale_date = models.DateField(default=date.today)

    birds_sold = models.PositiveIntegerField()
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



from django.utils import timezone

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