from decimal import Decimal

from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from api.models.sensor import Shed


ZERO = Decimal("0.00")


class Goat(models.Model):
    SEX_CHOICES = [
        ("male", "Male"),
        ("female", "Female"),
    ]

    STATUS_CHOICES = [
        ("active", "Active"),
        ("sold", "Sold"),
        ("deceased", "Deceased"),
    ]

    ACQUISITION_CHOICES = [
        ("purchased", "Purchased"),
        ("born_on_farm", "Born on Farm"),
    ]

    goat_code = models.CharField(
        max_length=20,
        unique=True,
        blank=True,
        editable=False,
    )

    name = models.CharField(max_length=100, blank=True)
    breed = models.CharField(max_length=100, blank=True)

    sex = models.CharField(
        max_length=10,
        choices=SEX_CHOICES,
    )

    owner = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="owned_goats",
    )

    # Real farm Shed relation. The old label is retained for backward
    # compatibility with already-created goat rows and old templates.
    shed = models.ForeignKey(
        Shed,
        on_delete=models.PROTECT,
        related_name="goats",
        null=True,
        blank=True,
        limit_choices_to={"shed_type": "goat"},
    )

    shed_label = models.CharField(
        max_length=80,
        default="Shed 3",
        help_text="Kept separate from poultry Shed records for now.",
    )

    acquisition_type = models.CharField(
        max_length=20,
        choices=ACQUISITION_CHOICES,
        default="purchased",
    )

    purchase_date = models.DateField(default=timezone.localdate)
    date_of_birth = models.DateField(blank=True, null=True)

    purchase_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=ZERO,
        validators=[MinValueValidator(ZERO)],
    )

    purchase_weight_kg = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        blank=True,
        null=True,
        validators=[MinValueValidator(Decimal("0.01"))],
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="active",
    )

    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_goats",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["goat_code", "id"]
        verbose_name = "Goat"
        verbose_name_plural = "Goats"

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        if self.shed:
            self.shed_label = self.shed.name
        super().save(*args, **kwargs)

        if is_new and not self.goat_code:
            self.goat_code = f"RN-{self.pk:04d}"
            super().save(update_fields=["goat_code", "updated_at"])

    @property
    def display_name(self):
        if self.name:
            return f"{self.goat_code} · {self.name}"
        return self.goat_code

    @property
    def owner_display_name(self):
        full_name = self.owner.get_full_name().strip()
        return full_name or self.owner.username

    @property
    def location_name(self):
        if self.shed_id:
            return self.shed.name
        return self.shed_label or "Shed 3"

    @property
    def current_weight_kg(self):
        latest = self.weight_records.order_by(
            "-record_date",
            "-id",
        ).first()

        if latest is not None:
            return latest.weight_kg

        return self.purchase_weight_kg

    def __str__(self):
        return f"{self.display_name} - {self.owner_display_name}"


class GoatWeightRecord(models.Model):
    goat = models.ForeignKey(
        Goat,
        on_delete=models.CASCADE,
        related_name="weight_records",
    )

    record_date = models.DateField(default=timezone.localdate)

    weight_kg = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )

    notes = models.TextField(blank=True)

    recorded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_goat_weights",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-record_date", "-id"]
        verbose_name = "Goat Weight Record"
        verbose_name_plural = "Goat Weight Records"

    def __str__(self):
        return f"{self.goat.goat_code} - {self.weight_kg} kg - {self.record_date}"


class GoatCostEntry(models.Model):
    CATEGORY_CHOICES = [
        ("feed", "Feed"),
        ("medicine", "Medicine"),
        ("expense", "Expense"),
    ]

    ALLOCATION_SCOPE_CHOICES = [
        ("individual", "One Goat"),
        ("selected", "Selected Goats"),
        ("all_active", "All Active Goats"),
    ]

    MEDICINE_TYPE_CHOICES = [
        ("medicine", "Medicine"),
        ("vaccine", "Vaccine"),
        ("multivitamin", "Multivitamin"),
    ]

    EXPENSE_CATEGORY_CHOICES = [
        ("diesel", "Diesel"),
        ("labor", "Labor"),
        ("electricity", "Electricity"),
        ("transport", "Transport"),
        ("maintenance", "Maintenance"),
        ("rent", "Farm Rent"),
        ("internet", "Internet"),
        ("service", "Service Charges"),
        ("misc", "Misc"),
    ]

    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    shed = models.ForeignKey(
        Shed,
        on_delete=models.PROTECT,
        related_name="goat_cost_entries",
        limit_choices_to={"shed_type": "goat"},
    )
    entry_date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )

    title = models.CharField(max_length=140, blank=True)
    medicine_type = models.CharField(
        max_length=20,
        choices=MEDICINE_TYPE_CHOICES,
        blank=True,
    )
    expense_category = models.CharField(
        max_length=20,
        choices=EXPENSE_CATEGORY_CHOICES,
        blank=True,
    )
    allocation_scope = models.CharField(
        max_length=20,
        choices=ALLOCATION_SCOPE_CHOICES,
        default="all_active",
    )
    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_goat_cost_entries",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-entry_date", "-id"]
        verbose_name = "Goat Cost Entry"
        verbose_name_plural = "Goat Cost Entries"

    @property
    def display_title(self):
        if self.title:
            return self.title
        if self.category == "feed":
            return "Goat Feed"
        if self.category == "medicine":
            return self.get_medicine_type_display() or "Medicine"
        if self.category == "expense":
            return self.get_expense_category_display() or "Expense"
        return self.get_category_display()

    def __str__(self):
        return f"{self.get_category_display()} - Rs {self.amount} - {self.entry_date}"


class GoatCostAllocation(models.Model):
    cost_entry = models.ForeignKey(
        GoatCostEntry,
        on_delete=models.CASCADE,
        related_name="allocations",
    )
    goat = models.ForeignKey(
        Goat,
        on_delete=models.CASCADE,
        related_name="cost_allocations",
    )
    owner_snapshot = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="goat_cost_allocations",
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(ZERO)],
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["cost_entry__entry_date", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["cost_entry", "goat"],
                name="unique_goat_cost_allocation",
            )
        ]

    def __str__(self):
        return f"{self.goat.goat_code} - {self.cost_entry.get_category_display()} - Rs {self.amount}"


class GoatSale(models.Model):
    PAYMENT_METHOD_CHOICES = [
        ("cash", "Cash"),
        ("bank_transfer", "Bank Transfer"),
        ("cheque", "Cheque"),
        ("credit", "Credit"),
        ("other", "Other"),
    ]

    goat = models.OneToOneField(
        Goat,
        on_delete=models.PROTECT,
        related_name="sale_record",
    )
    sale_date = models.DateField(default=timezone.localdate)
    buyer_name = models.CharField(max_length=140, blank=True)
    sale_weight_kg = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    rate_per_kg = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    gross_amount = models.DecimalField(max_digits=14, decimal_places=2)
    discount_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=ZERO,
        validators=[MinValueValidator(ZERO)],
    )
    total_amount = models.DecimalField(max_digits=14, decimal_places=2)
    locked_total_cost = models.DecimalField(max_digits=14, decimal_places=2)
    payment_method = models.CharField(
        max_length=20,
        choices=PAYMENT_METHOD_CHOICES,
        default="cash",
    )
    notes = models.TextField(blank=True)
    recorded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_goat_sales",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-sale_date", "-id"]
        verbose_name = "Goat Sale"
        verbose_name_plural = "Goat Sales"

    @property
    def profit(self):
        return (self.total_amount or ZERO) - (self.locked_total_cost or ZERO)

    @property
    def roi(self):
        if not self.locked_total_cost:
            return ZERO
        return (self.profit / self.locked_total_cost) * Decimal("100")

    def __str__(self):
        return f"{self.goat.goat_code} sale - Rs {self.total_amount}"


class GoatAccountPayment(models.Model):
    PAYMENT_METHOD_CHOICES = [
        ("cash", "Cash"),
        ("bank_transfer", "Bank Transfer"),
        ("cheque", "Cheque"),
        ("other", "Other"),
    ]

    owner = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="goat_account_payments",
    )
    payment_date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    payment_method = models.CharField(
        max_length=20,
        choices=PAYMENT_METHOD_CHOICES,
        default="bank_transfer",
    )
    reference = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    recorded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_goat_account_payments",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-payment_date", "-id"]
        verbose_name = "Goat Account Payment"
        verbose_name_plural = "Goat Account Payments"

    def __str__(self):
        owner_name = self.owner.get_full_name().strip() or self.owner.username
        return f"{owner_name} - Rs {self.amount}"
