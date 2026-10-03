from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


ZERO = Decimal("0.00")


class ChartOfAccount(models.Model):
    TYPE_CHOICES = [
        ("asset", "Asset"),
        ("liability", "Liability"),
        ("equity", "Equity"),
        ("revenue", "Revenue"),
        ("cogs", "Cost of Goods Sold"),
        ("expense", "Expense"),
    ]
    NORMAL_BALANCE_CHOICES = [
        ("debit", "Debit"),
        ("credit", "Credit"),
    ]

    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=160)
    account_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    normal_balance = models.CharField(max_length=10, choices=NORMAL_BALANCE_CHOICES)
    is_active = models.BooleanField(default=True)
    is_system = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code", "id"]
        verbose_name = "Chart of Account"
        verbose_name_plural = "Chart of Accounts"

    def __str__(self):
        return f"{self.code} - {self.name}"


class JournalEntry(models.Model):
    MODULE_CHOICES = [
        ("egg_pos", "Egg POS"),
        ("poultry", "Poultry"),
        ("goat", "Goat"),
        ("general", "General"),
    ]

    entry_date = models.DateField(default=timezone.localdate)
    reference = models.CharField(max_length=120, blank=True)
    memo = models.CharField(max_length=255, blank=True)
    module = models.CharField(max_length=20, choices=MODULE_CHOICES, default="general")
    source_type = models.CharField(max_length=60, blank=True)
    source_id = models.PositiveBigIntegerField(null=True, blank=True)
    source_key = models.CharField(max_length=160, unique=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="journal_entries_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["entry_date", "id"]
        indexes = [
            models.Index(fields=["module", "entry_date"], name="api_je_module_date_idx"),
            models.Index(fields=["source_type", "source_id"], name="api_je_source_idx"),
        ]

    @property
    def total_debit(self):
        return sum((Decimal(line.debit or 0) for line in self.lines.all()), ZERO)

    @property
    def total_credit(self):
        return sum((Decimal(line.credit or 0) for line in self.lines.all()), ZERO)

    def __str__(self):
        return self.reference or self.source_key


class JournalLine(models.Model):
    PARTY_TYPE_CHOICES = [
        ("", "None"),
        ("supplier", "Supplier"),
        ("customer", "Customer"),
        ("user", "Staff / Salesperson"),
        ("farm", "Farm / Internal Unit"),
    ]

    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(ChartOfAccount, on_delete=models.PROTECT, related_name="journal_lines")
    description = models.CharField(max_length=255, blank=True)
    debit = models.DecimalField(max_digits=16, decimal_places=2, default=ZERO)
    credit = models.DecimalField(max_digits=16, decimal_places=2, default=ZERO)
    party_type = models.CharField(max_length=20, choices=PARTY_TYPE_CHOICES, blank=True, default="")
    party_id = models.PositiveBigIntegerField(null=True, blank=True)
    party_name = models.CharField(max_length=180, blank=True)

    class Meta:
        ordering = ["entry__entry_date", "entry_id", "id"]
        indexes = [
            models.Index(fields=["account", "party_type", "party_id"], name="api_jl_acct_party_idx"),
            models.Index(fields=["party_type", "party_id"], name="api_jl_party_idx"),
        ]

    def clean(self):
        super().clean()
        debit = Decimal(self.debit or 0)
        credit = Decimal(self.credit or 0)
        if debit < ZERO or credit < ZERO:
            raise ValidationError("Journal debit and credit cannot be negative.")
        if (debit > ZERO and credit > ZERO) or (debit <= ZERO and credit <= ZERO):
            raise ValidationError("Each journal line must contain either a debit or a credit.")

    def __str__(self):
        side = self.debit if self.debit else self.credit
        return f"{self.account.code} - {side}"
