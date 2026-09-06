from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from api.models.sensor import Batch


class EggProductionEntry(models.Model):
    batch = models.ForeignKey(
        Batch,
        on_delete=models.CASCADE,
        related_name="egg_production_entries",
    )

    production_date = models.DateField(default=timezone.localdate)

    eggs_collected = models.PositiveIntegerField(default=0)
    damaged_eggs = models.PositiveIntegerField(default=0)

    notes = models.TextField(blank=True)

    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_egg_production_entries",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-production_date", "-id"]
        verbose_name = "Egg Production Entry"
        verbose_name_plural = "Egg Production Entries"

    @property
    def usable_eggs(self):
        return max(
            int(self.eggs_collected or 0) - int(self.damaged_eggs or 0),
            0,
        )

    def clean(self):
        super().clean()

        if self.damaged_eggs > self.eggs_collected:
            raise ValidationError(
                {"damaged_eggs": "Damaged eggs cannot exceed eggs collected."}
            )

        if self.batch_id and self.batch.shed.shed_type != "layer":
            raise ValidationError(
                {"batch": "Egg production can only be recorded for a Layer shed batch."}
            )

    def __str__(self):
        return (
            f"Batch {self.batch.batch_number} - "
            f"{self.production_date} - {self.usable_eggs} usable eggs"
        )


class EggSale(models.Model):
    PAYMENT_METHOD_CHOICES = [
        ("cash", "Cash"),
        ("bank_transfer", "Bank Transfer"),
        ("credit", "Credit"),
        ("other", "Other"),
    ]

    batch = models.ForeignKey(
        Batch,
        on_delete=models.CASCADE,
        related_name="egg_sales",
    )

    sale_date = models.DateField(default=timezone.localdate)

    buyer_name = models.CharField(
        max_length=150,
        blank=True,
        help_text="Optional buyer/customer name.",
    )

    eggs_sold = models.PositiveIntegerField()

    rate_per_egg = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )

    discount_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    payment_method = models.CharField(
        max_length=20,
        choices=PAYMENT_METHOD_CHOICES,
        default="cash",
    )

    notes = models.TextField(blank=True)

    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_egg_sales",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-sale_date", "-id"]
        verbose_name = "Egg Sale"
        verbose_name_plural = "Egg Sales"

    @property
    def gross_amount(self):
        return (
            Decimal(self.eggs_sold or 0)
            * Decimal(self.rate_per_egg or 0)
        ).quantize(Decimal("0.01"))

    @property
    def total_amount(self):
        return max(
            self.gross_amount - Decimal(self.discount_amount or 0),
            Decimal("0.00"),
        ).quantize(Decimal("0.01"))

    def clean(self):
        super().clean()

        if self.batch_id and self.batch.shed.shed_type != "layer":
            raise ValidationError(
                {"batch": "Egg sales can only be recorded for a Layer shed batch."}
            )

        if self.eggs_sold is not None and self.eggs_sold <= 0:
            raise ValidationError({"eggs_sold": "Egg quantity must be greater than zero."})

        if self.rate_per_egg is not None and self.rate_per_egg <= 0:
            raise ValidationError({"rate_per_egg": "Rate per egg must be greater than zero."})

        if self.discount_amount is not None and self.discount_amount < 0:
            raise ValidationError({"discount_amount": "Discount cannot be negative."})

        if (
            self.eggs_sold
            and self.rate_per_egg
            and self.discount_amount is not None
            and self.discount_amount > self.gross_amount
        ):
            raise ValidationError(
                {"discount_amount": "Discount cannot exceed the gross egg sale amount."}
            )

    def __str__(self):
        return (
            f"Batch {self.batch.batch_number} - "
            f"{self.sale_date} - {self.eggs_sold} eggs"
        )
