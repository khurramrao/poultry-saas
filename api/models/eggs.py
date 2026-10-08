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
    is_voided = models.BooleanField(default=False)
    void_reason = models.CharField(max_length=255, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="voided_direct_egg_sales",
    )

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

class LayerHenCountHistory(models.Model):
    """Effective-dated laying-hen count for a Layer batch.

    The count is entered only when it changes. Egg-production percentages for
    any date use the latest count whose effective_date is on or before that
    production date. Roosters are intentionally excluded.
    """

    batch = models.ForeignKey(
        Batch,
        on_delete=models.CASCADE,
        related_name="active_hen_history",
    )
    effective_date = models.DateField(default=timezone.localdate)
    active_hens = models.PositiveIntegerField()
    notes = models.CharField(max_length=255, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_layer_hen_counts",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-effective_date", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["batch", "effective_date"],
                name="unique_layer_hen_count_per_batch_date",
            )
        ]
        verbose_name = "Layer Active Hen Count"
        verbose_name_plural = "Layer Active Hen Counts"

    def clean(self):
        super().clean()
        if self.batch_id and self.batch.shed.shed_type != "layer":
            raise ValidationError(
                {"batch": "Active hen counts can only be recorded for a Layer shed batch."}
            )
        if not self.active_hens or self.active_hens <= 0:
            raise ValidationError({"active_hens": "Active hens must be greater than zero."})
        if self.batch_id and self.active_hens > int(self.batch.bird_count_initial or 0):
            raise ValidationError(
                {"active_hens": "Active hens cannot exceed the batch starting bird count."}
            )
        if self.effective_date and self.effective_date > timezone.localdate():
            raise ValidationError({"effective_date": "Effective date cannot be in the future."})

    def __str__(self):
        return (
            f"Batch {self.batch.batch_number} - {self.effective_date} - "
            f"{self.active_hens} active hens"
        )



class EggStockWastage(models.Model):
    """Post-collection damage. Separate from damage counted on production day."""
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, related_name="egg_stock_wastage")
    damage_date = models.DateField(default=timezone.localdate)
    quantity = models.PositiveIntegerField()
    reason = models.CharField(max_length=255)
    source_sale = models.OneToOneField(
        EggSale, null=True, blank=True, on_delete=models.PROTECT,
        related_name="wastage_reclassification",
    )
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
        related_name="egg_wastage_recorded",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-damage_date", "-id"]

    def clean(self):
        super().clean()
        if not self.quantity or self.quantity <= 0:
            raise ValidationError({"quantity": "Damaged quantity must be positive."})
        if self.batch_id and self.batch.shed.shed_type != "layer":
            raise ValidationError({"batch": "Damaged eggs must belong to a Layer batch."})


class EggSaleCorrectionAudit(models.Model):
    ACTIONS = [
        ("edit", "Edit"), ("reverse", "Reverse"), ("restore", "Undo reversal"),
        ("damage", "Convert to damage"),
    ]
    sale = models.ForeignKey(EggSale, on_delete=models.PROTECT, related_name="correction_audit")
    action = models.CharField(max_length=12, choices=ACTIONS)
    reason = models.CharField(max_length=255)
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    admin = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="direct_egg_sale_corrections",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class FarmEggCashMovement(models.Model):
    """Admin-posted farm egg cash/bank movements, distinct from farm profit/COGS.

    POS transfer payments and direct-sale collections are NEVER copied here;
    the cashbook reads those original transaction tables directly.
    """
    MOVEMENT_TYPES = [
        ("opening", "Opening balance (not previously recorded)"),
        ("capital_in", "Capital introduced"),
        ("other_in", "Other cash received"),
        ("expense", "Payment for an already recorded farm expense"),
        ("withdrawal", "Owner/investor withdrawal"),
        ("other_out", "Other payment out"),
        ("cash_to_bank", "Transfer physical cash to bank"),
        ("bank_to_cash", "Withdraw bank funds into physical cash"),
    ]
    ACCOUNT_CHOICES = [("cash", "Physical cash"), ("bank", "Bank / wallet")]
    movement_date = models.DateField(default=timezone.localdate)
    movement_type = models.CharField(max_length=22, choices=MOVEMENT_TYPES)
    account = models.CharField(max_length=8, choices=ACCOUNT_CHOICES, default="cash")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    batch = models.ForeignKey(
        Batch, null=True, blank=True, on_delete=models.PROTECT,
        related_name="egg_cash_movements",
    )
    reference = models.CharField(max_length=120, blank=True)
    notes = models.CharField(max_length=255)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="farm_egg_cash_movements_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    is_voided = models.BooleanField(default=False)
    void_reason = models.CharField(max_length=255, blank=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="farm_egg_cash_movements_voided",
    )
    voided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-movement_date", "-id"]

    def clean(self):
        super().clean()
        if self.amount is None or self.amount <= 0:
            raise ValidationError({"amount": "The amount must be greater than zero."})
        if self.movement_date and self.movement_date > timezone.localdate():
            raise ValidationError({"movement_date": "A future date is not allowed."})
        if self.batch_id and self.batch.shed.shed_type != "layer":
            raise ValidationError({"batch": "Choose a layer batch."})
        if self.movement_type in ("cash_to_bank", "bank_to_cash"):
            # A transfer is ALWAYS represented by two sides in the reporting
            # service, but only one source model row, preventing duplicates.
            self.account = "cash" if self.movement_type == "cash_to_bank" else "bank"
