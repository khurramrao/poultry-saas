from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from api.models.sensor import Batch


MONEY_ZERO = Decimal("0.00")


class EggPOSProduct(models.Model):
    name = models.CharField(max_length=120, unique=True)
    sku = models.CharField(max_length=40, unique=True)
    weight_grams = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)
    default_sale_price = models.DecimalField(max_digits=10, decimal_places=2, default=MONEY_ZERO)
    low_stock_eggs = models.PositiveIntegerField(default=100)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        verbose_name = "Egg POS Product"
        verbose_name_plural = "Egg POS Products"

    def __str__(self):
        return f"{self.name} ({self.sku})"


class EggPOSSupplier(models.Model):
    name = models.CharField(max_length=150, unique=True)
    phone = models.CharField(max_length=40, blank=True)
    address = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        verbose_name = "Egg POS Supplier"
        verbose_name_plural = "Egg POS Suppliers"

    def __str__(self):
        return self.name




class EggPOSCustomer(models.Model):
    name = models.CharField(max_length=150)
    company_name = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    address = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="egg_pos_customers_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "company_name", "id"]
        verbose_name = "Egg POS Customer"
        verbose_name_plural = "Egg POS Customers"

    @property
    def display_label(self):
        if self.company_name:
            return f"{self.name} — {self.company_name}"
        return self.name

    def __str__(self):
        return self.display_label


class EggPOSUserAccess(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="egg_pos_access",
    )
    can_sell = models.BooleanField(default=True)
    can_manage = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    commission_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=MONEY_ZERO,
        help_text="Percentage of commissionable profit earned by this salesperson.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Egg POS User Access"
        verbose_name_plural = "Egg POS User Access"

    def __str__(self):
        return f"{self.user.username} - Egg POS"


class EggPOSPurchase(models.Model):
    TRANSPORT_PAYMENT_CHOICES = [
        ("cash", "Company Cash"),
        ("bank_transfer", "Bank Transfer"),
        ("supplier_payable", "Add to Supplier Payable"),
        ("other", "Other Company Payment"),
    ]

    supplier = models.ForeignKey(
        EggPOSSupplier,
        on_delete=models.PROTECT,
        related_name="egg_pos_purchases",
    )
    purchase_date = models.DateField(default=timezone.localdate)
    reference = models.CharField(max_length=120, blank=True)
    transport_cost = models.DecimalField(max_digits=14, decimal_places=2, default=MONEY_ZERO)
    transport_payment_method = models.CharField(
        max_length=30,
        choices=TRANSPORT_PAYMENT_CHOICES,
        default="cash",
    )
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="egg_pos_purchases_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-purchase_date", "-id"]

    def __str__(self):
        return f"Purchase #{self.id or '-'} - {self.supplier}"

    @property
    def total_amount(self):
        """Base supplier invoice value, excluding inbound transport."""
        return sum((item.line_total for item in self.items.all()), MONEY_ZERO)

    @property
    def landed_total(self):
        return Decimal(self.total_amount or 0) + Decimal(self.transport_cost or 0)

    @property
    def amount_paid(self):
        if not self.pk:
            return MONEY_ZERO
        return sum((Decimal(payment.amount or 0) for payment in self.payments.all()), MONEY_ZERO)

    @property
    def balance_due(self):
        payable = Decimal(self.total_amount or 0)
        if self.transport_payment_method == "supplier_payable":
            payable += Decimal(self.transport_cost or 0)
        return max(payable - Decimal(self.amount_paid or 0), MONEY_ZERO)

    @property
    def payable_total(self):
        total = Decimal(self.total_amount or 0)
        if self.transport_payment_method == "supplier_payable":
            total += Decimal(self.transport_cost or 0)
        return total

    @property
    def payment_status(self):
        paid = Decimal(self.amount_paid or 0)
        total = Decimal(self.payable_total or 0)
        if total <= MONEY_ZERO or paid >= total:
            return "paid"
        return "partial" if paid > MONEY_ZERO else "unpaid"

    @property
    def payment_status_label(self):
        return {
            "paid": "Paid",
            "partial": "Partially Paid",
            "unpaid": "Unpaid",
        }[self.payment_status]


class EggPOSFarmTransfer(models.Model):
    TRANSPORT_PAYMENT_CHOICES = [
        ("cash", "Company Cash"),
        ("bank_transfer", "Bank Transfer"),
        ("other", "Other Company Payment"),
    ]

    batch = models.ForeignKey(
        Batch,
        on_delete=models.PROTECT,
        related_name="egg_pos_transfers",
    )
    transfer_number = models.CharField(max_length=30, unique=True, null=True, blank=True)
    transfer_date = models.DateField(default=timezone.localdate)
    payment_due_date = models.DateField(null=True, blank=True)
    transport_cost = models.DecimalField(max_digits=14, decimal_places=2, default=MONEY_ZERO)
    transport_payment_method = models.CharField(
        max_length=30,
        choices=TRANSPORT_PAYMENT_CHOICES,
        default="cash",
    )
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="egg_pos_farm_transfers_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-transfer_date", "-id"]

    def clean(self):
        super().clean()
        if self.batch_id and self.batch.shed.shed_type != "layer":
            raise ValidationError({"batch": "Farm egg transfer requires a Layer shed batch."})

    @property
    def total_quantity(self):
        return sum((int(item.quantity or 0) for item in self.items.all()), 0)

    @property
    def total_amount(self):
        """Internal amount payable to RayNoor Egg Production, excluding transport."""
        return sum((item.line_total for item in self.items.all()), MONEY_ZERO)

    @property
    def landed_total(self):
        return Decimal(self.total_amount or 0) + Decimal(self.transport_cost or 0)

    @property
    def amount_paid(self):
        if not self.pk:
            return MONEY_ZERO
        return sum((Decimal(payment.amount or 0) for payment in self.payments.all()), MONEY_ZERO)

    @property
    def balance_due(self):
        return max(Decimal(self.total_amount or 0) - Decimal(self.amount_paid or 0), MONEY_ZERO)

    @property
    def payment_status(self):
        paid = Decimal(self.amount_paid or 0)
        total = Decimal(self.total_amount or 0)
        if total <= MONEY_ZERO or paid >= total:
            return "paid"
        return "partial" if paid > MONEY_ZERO else "unpaid"

    @property
    def payment_status_label(self):
        return {
            "paid": "Paid",
            "partial": "Partially Paid",
            "unpaid": "Unpaid",
        }[self.payment_status]

    def __str__(self):
        return self.transfer_number or f"Farm Egg Transfer #{self.id or '-'}"


class EggPOSFarmTransferPayment(models.Model):
    PAYMENT_METHOD_CHOICES = [
        ("cash", "Cash"),
        ("bank_transfer", "Bank Transfer"),
        ("easypaisa", "Easypaisa"),
        ("jazzcash", "JazzCash"),
        ("other", "Other"),
    ]

    transfer = models.ForeignKey(
        EggPOSFarmTransfer,
        on_delete=models.CASCADE,
        related_name="payments",
    )
    payment_date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    payment_method = models.CharField(
        max_length=30,
        choices=PAYMENT_METHOD_CHOICES,
        default="bank_transfer",
    )
    reference = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="egg_pos_farm_transfer_payments_recorded",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["payment_date", "id"]

    def clean(self):
        super().clean()
        if self.amount is None or self.amount <= MONEY_ZERO:
            raise ValidationError({"amount": "Payment amount must be greater than zero."})

    def __str__(self):
        return f"{self.transfer.transfer_number or self.transfer_id} - Rs {self.amount}"


class EggPOSInventoryLot(models.Model):
    SOURCE_CHOICES = [
        ("purchase", "Purchased Eggs"),
        ("farm", "RayNoor Farm Eggs"),
    ]

    product = models.ForeignKey(
        EggPOSProduct,
        on_delete=models.PROTECT,
        related_name="inventory_lots",
    )
    source_type = models.CharField(max_length=20, choices=SOURCE_CHOICES)
    supplier = models.ForeignKey(
        EggPOSSupplier,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="egg_pos_inventory_lots",
    )
    farm_batch = models.ForeignKey(
        Batch,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="egg_pos_inventory_lots",
    )
    purchase = models.ForeignKey(
        EggPOSPurchase,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="inventory_lots",
    )
    farm_transfer = models.ForeignKey(
        EggPOSFarmTransfer,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="inventory_lots",
    )
    lot_code = models.CharField(max_length=60, unique=True)
    received_date = models.DateField(default=timezone.localdate)
    expiry_date = models.DateField(null=True, blank=True)
    quantity_received = models.PositiveIntegerField()
    quantity_remaining = models.PositiveIntegerField()
    unit_cost = models.DecimalField(max_digits=14, decimal_places=6)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["received_date", "id"]

    def clean(self):
        super().clean()
        if self.quantity_received <= 0:
            raise ValidationError({"quantity_received": "Quantity must be greater than zero."})
        if self.quantity_remaining < 0 or self.quantity_remaining > self.quantity_received:
            raise ValidationError({"quantity_remaining": "Remaining quantity is invalid."})
        if self.unit_cost is None or self.unit_cost < 0:
            raise ValidationError({"unit_cost": "Unit cost cannot be negative."})
        if self.source_type == "purchase" and not self.supplier_id:
            raise ValidationError({"supplier": "Purchased egg lots require a supplier."})
        if self.source_type == "farm" and not self.farm_batch_id:
            raise ValidationError({"farm_batch": "Farm egg lots require a Layer batch."})

    @property
    def stock_value(self):
        return Decimal(self.quantity_remaining or 0) * Decimal(self.unit_cost or 0)

    def __str__(self):
        return f"{self.lot_code} - {self.product.name} - {self.quantity_remaining} eggs"


class EggPOSPurchaseItem(models.Model):
    purchase = models.ForeignKey(EggPOSPurchase, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(EggPOSProduct, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    unit_cost = models.DecimalField(max_digits=14, decimal_places=6)
    lot = models.OneToOneField(
        EggPOSInventoryLot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_item",
    )

    @property
    def line_total(self):
        return Decimal(self.quantity or 0) * Decimal(self.unit_cost or 0)

    def __str__(self):
        return f"{self.product.name} x {self.quantity}"


class EggPOSFarmTransferItem(models.Model):
    transfer = models.ForeignKey(EggPOSFarmTransfer, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(EggPOSProduct, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    unit_cost = models.DecimalField(
        max_digits=14,
        decimal_places=6,
        help_text="Base transfer cost per egg before inbound transport allocation.",
    )
    lot = models.OneToOneField(
        EggPOSInventoryLot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="farm_transfer_item",
    )

    @property
    def line_total(self):
        return Decimal(self.quantity or 0) * Decimal(self.unit_cost or 0)

    def __str__(self):
        return f"{self.product.name} x {self.quantity}"


class EggPOSSale(models.Model):
    PAYMENT_METHOD_CHOICES = [
        ("cash", "Cash"),
        ("bank_transfer", "Bank Transfer"),
        ("credit", "Credit"),
        ("other", "Other"),
    ]

    sale_number = models.CharField(max_length=40, unique=True, null=True, blank=True)
    sale_date = models.DateField(default=timezone.localdate)
    customer = models.ForeignKey(
        EggPOSCustomer,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="sales",
    )
    customer_name = models.CharField(max_length=150, blank=True)
    customer_phone = models.CharField(max_length=40, blank=True)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, default="cash")
    credit_due_date = models.DateField(null=True, blank=True)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=MONEY_ZERO)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=MONEY_ZERO)
    net_total = models.DecimalField(max_digits=14, decimal_places=2, default=MONEY_ZERO)
    cogs_total = models.DecimalField(max_digits=14, decimal_places=2, default=MONEY_ZERO)
    profit_total = models.DecimalField(max_digits=14, decimal_places=2, default=MONEY_ZERO)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="egg_pos_sales",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-sale_date", "-id"]

    def __str__(self):
        return self.sale_number or f"Egg POS Sale #{self.id or '-'}"

    @property
    def amount_paid(self):
        if not self.pk:
            return MONEY_ZERO
        return sum((Decimal(payment.amount or 0) for payment in self.payments.all()), MONEY_ZERO)

    @property
    def balance_due(self):
        return max(Decimal(self.net_total or 0) - Decimal(self.amount_paid or 0), MONEY_ZERO)

    @property
    def payment_status(self):
        paid = Decimal(self.amount_paid or 0)
        total = Decimal(self.net_total or 0)
        if total <= MONEY_ZERO or paid >= total:
            return "paid"
        return "partial" if paid > MONEY_ZERO else "unpaid"

    @property
    def payment_status_label(self):
        return {"paid": "Paid", "partial": "Partially Paid", "unpaid": "Unpaid / Credit"}[self.payment_status]


class EggPOSSalePayment(models.Model):
    PAYMENT_METHOD_CHOICES = [
        ("cash", "Cash"),
        ("bank_transfer", "Bank Transfer"),
        ("easypaisa", "Easypaisa"),
        ("jazzcash", "JazzCash"),
        ("other", "Other"),
    ]
    sale = models.ForeignKey(EggPOSSale, on_delete=models.CASCADE, related_name="payments")
    payment_date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    payment_method = models.CharField(max_length=30, choices=PAYMENT_METHOD_CHOICES, default="cash")
    reference = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="egg_pos_sale_payments_recorded")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["payment_date", "id"]

    def clean(self):
        super().clean()
        if self.amount is None or self.amount <= MONEY_ZERO:
            raise ValidationError({"amount": "Payment amount must be greater than zero."})

    def __str__(self):
        return f"{self.sale.sale_number} - Rs {self.amount}"


class EggPOSSaleItem(models.Model):
    SALE_UNIT_CHOICES = [
        ("egg", "Loose Egg"),
        ("dozen", "Dozen (12 eggs)"),
        ("tray", "Tray (30 eggs)"),
        ("crate", "Paiti / Crate (360 eggs)"),
    ]

    sale = models.ForeignKey(EggPOSSale, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(EggPOSProduct, on_delete=models.PROTECT)
    # Physical inventory quantity is ALWAYS stored as individual eggs so FIFO/COGS
    # remains exact irrespective of how the salesperson quoted the sale.
    quantity = models.PositiveIntegerField()
    # Snapshot of the selling unit used on the invoice. Legacy rows use loose eggs.
    sale_unit = models.CharField(max_length=20, choices=SALE_UNIT_CHOICES, default="egg")
    unit_count = models.PositiveIntegerField(default=1)
    unit_size = models.PositiveIntegerField(default=1)
    unit_rate = models.DecimalField(max_digits=12, decimal_places=2, default=MONEY_ZERO)
    # Compatibility field: effective selling price per egg. New invoices should show
    # unit_rate + sale_unit because pack prices may not divide evenly per egg.
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    line_total = models.DecimalField(max_digits=14, decimal_places=2, default=MONEY_ZERO)
    cogs_amount = models.DecimalField(max_digits=14, decimal_places=2, default=MONEY_ZERO)

    @property
    def gross_profit_before_sale_discount(self):
        return Decimal(self.line_total or 0) - Decimal(self.cogs_amount or 0)

    @property
    def sale_unit_short_label(self):
        return {
            "egg": "egg",
            "dozen": "dozen",
            "tray": "tray",
            "crate": "paiti / crate",
        }.get(self.sale_unit, "egg")

    @property
    def display_sale_quantity(self):
        count = int(self.unit_count or 0)
        label = self.sale_unit_short_label
        if self.sale_unit == "egg":
            label = "egg" if count == 1 else "eggs"
            return f"{count} {label}"
        if count != 1:
            if self.sale_unit == "dozen":
                label = "dozen"
            elif self.sale_unit == "tray":
                label = "trays"
            else:
                label = "paiti / crates"
        return f"{count} {label} ({self.quantity} eggs)"

    def __str__(self):
        return f"{self.product.name} - {self.display_sale_quantity}"


class EggPOSSaleAllocation(models.Model):
    sale_item = models.ForeignKey(
        EggPOSSaleItem,
        on_delete=models.CASCADE,
        related_name="lot_allocations",
    )
    lot = models.ForeignKey(
        EggPOSInventoryLot,
        on_delete=models.PROTECT,
        related_name="sale_allocations",
    )
    quantity = models.PositiveIntegerField()
    unit_cost = models.DecimalField(max_digits=14, decimal_places=6)
    cogs_amount = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ["lot__received_date", "lot_id"]

    def __str__(self):
        return f"{self.sale_item} <- {self.lot.lot_code}"


class EggPOSSupplierPayment(models.Model):
    PAYMENT_METHOD_CHOICES = [
        ("cash", "Cash"),
        ("bank_transfer", "Bank Transfer"),
        ("easypaisa", "Easypaisa"),
        ("jazzcash", "JazzCash"),
        ("other", "Other"),
    ]

    supplier = models.ForeignKey(
        EggPOSSupplier,
        on_delete=models.PROTECT,
        related_name="payments",
    )
    purchase = models.ForeignKey(
        EggPOSPurchase,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payments",
        help_text="Optional purchase invoice this payment relates to.",
    )
    payment_date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    payment_method = models.CharField(max_length=30, choices=PAYMENT_METHOD_CHOICES, default="cash")
    reference = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="egg_pos_supplier_payments_recorded",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["payment_date", "id"]

    def clean(self):
        super().clean()
        if self.amount is None or self.amount <= MONEY_ZERO:
            raise ValidationError({"amount": "Payment amount must be greater than zero."})
        if self.purchase_id and self.supplier_id and self.purchase.supplier_id != self.supplier_id:
            raise ValidationError({"purchase": "Selected purchase belongs to a different supplier."})

    def __str__(self):
        return f"{self.supplier.name} - Rs {self.amount}"


class EggPOSExpense(models.Model):
    EXPENSE_TYPE_CHOICES = [
        ("fuel", "Fuel"),
        ("delivery", "Delivery / Rider"),
        ("toll_parking", "Toll / Parking"),
        ("packaging", "Packaging"),
        ("other", "Other Selling Expense"),
    ]
    PAYMENT_SOURCE_CHOICES = [
        ("sales_collection", "Deduct from Sales Collection"),
        ("personal", "Paid Personally - Reimburse Staff"),
        ("company_cash", "Paid by Company Cash"),
        ("company_bank", "Paid by Company Bank"),
    ]
    STATUS_CHOICES = [
        ("pending", "Pending Approval"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ]

    expense_date = models.DateField(default=timezone.localdate)
    expense_type = models.CharField(max_length=30, choices=EXPENSE_TYPE_CHOICES, default="fuel")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    used_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="egg_pos_expenses_used",
        help_text="Salesperson/staff member whose selling activity used this expense.",
    )
    payment_source = models.CharField(
        max_length=30,
        choices=PAYMENT_SOURCE_CHOICES,
        default="sales_collection",
    )
    affects_commission = models.BooleanField(default=True)
    reference = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    entered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="egg_pos_expenses_entered",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="egg_pos_expenses_approved",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-expense_date", "-id"]
        indexes = [
            models.Index(fields=["used_by", "expense_date", "status"], name="api_exp_user_date_idx"),
        ]

    def clean(self):
        super().clean()
        if self.amount is None or self.amount <= MONEY_ZERO:
            raise ValidationError({"amount": "Expense amount must be greater than zero."})

    def __str__(self):
        return f"{self.get_expense_type_display()} - {self.used_by.username} - Rs {self.amount}"


class EggPOSCashSettlement(models.Model):
    DESTINATION_CHOICES = [
        ("cash", "Main Cash"),
        ("bank_transfer", "Bank"),
    ]

    salesperson = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="egg_pos_cash_settlements",
    )
    settlement_date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    destination = models.CharField(max_length=30, choices=DESTINATION_CHOICES, default="cash")
    reference = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="egg_pos_cash_settlements_recorded",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-settlement_date", "-id"]

    def clean(self):
        super().clean()
        if self.amount is None or self.amount <= MONEY_ZERO:
            raise ValidationError({"amount": "Settlement amount must be greater than zero."})

    def __str__(self):
        return f"{self.salesperson.username} settlement - Rs {self.amount}"


class EggPOSCashHandoverRequest(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending Confirmation"),
        ("confirmed", "Confirmed"),
        ("rejected", "Rejected"),
    ]

    salesperson = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="egg_pos_cash_handover_requests",
    )
    handover_date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    destination = models.CharField(
        max_length=30,
        choices=EggPOSCashSettlement.DESTINATION_CHOICES,
        default="cash",
    )
    reference = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    submitted_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="egg_pos_cash_handover_requests_reviewed",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.CharField(max_length=255, blank=True)
    settlement = models.OneToOneField(
        EggPOSCashSettlement,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="handover_request",
    )

    class Meta:
        ordering = ["-handover_date", "-id"]
        indexes = [
            models.Index(fields=["salesperson", "status", "handover_date"], name="api_cashreq_user_idx"),
        ]

    def clean(self):
        super().clean()
        if self.amount is None or self.amount <= MONEY_ZERO:
            raise ValidationError({"amount": "Handover amount must be greater than zero."})

    def __str__(self):
        return f"{self.salesperson.username} handover request - Rs {self.amount}"


class EggPOSCommissionPeriod(models.Model):
    salesperson = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="egg_pos_commission_periods",
    )
    period_start = models.DateField()
    period_end = models.DateField()
    sales_revenue = models.DecimalField(max_digits=16, decimal_places=2, default=MONEY_ZERO)
    cogs = models.DecimalField(max_digits=16, decimal_places=2, default=MONEY_ZERO)
    selling_expenses = models.DecimalField(max_digits=16, decimal_places=2, default=MONEY_ZERO)
    commissionable_profit = models.DecimalField(max_digits=16, decimal_places=2, default=MONEY_ZERO)
    commission_percent = models.DecimalField(max_digits=5, decimal_places=2, default=MONEY_ZERO)
    commission_amount = models.DecimalField(max_digits=16, decimal_places=2, default=MONEY_ZERO)
    notes = models.TextField(blank=True)
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="egg_pos_commissions_posted",
    )
    posted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-period_end", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["salesperson", "period_start", "period_end"],
                name="uniq_egg_pos_commission_period",
            ),
        ]

    def clean(self):
        super().clean()
        if self.period_end and self.period_start and self.period_end < self.period_start:
            raise ValidationError({"period_end": "Period end cannot be before period start."})
        if self.commission_percent < MONEY_ZERO or self.commission_percent > Decimal("100.00"):
            raise ValidationError({"commission_percent": "Commission must be between 0% and 100%."})

    @property
    def amount_paid(self):
        if not self.pk:
            return MONEY_ZERO
        return sum((Decimal(payment.amount or 0) for payment in self.payments.all()), MONEY_ZERO)

    @property
    def balance_due(self):
        return max(Decimal(self.commission_amount or 0) - Decimal(self.amount_paid or 0), MONEY_ZERO)

    @property
    def payment_status(self):
        if self.balance_due <= MONEY_ZERO:
            return "paid"
        if self.amount_paid > MONEY_ZERO:
            return "partial"
        return "unpaid"

    def __str__(self):
        return f"{self.salesperson.username} {self.period_start} to {self.period_end}"


class EggPOSCommissionPayment(models.Model):
    PAYMENT_METHOD_CHOICES = [
        ("cash", "Cash"),
        ("bank_transfer", "Bank Transfer"),
        ("easypaisa", "Easypaisa"),
        ("jazzcash", "JazzCash"),
        ("other", "Other"),
    ]

    commission = models.ForeignKey(
        EggPOSCommissionPeriod,
        on_delete=models.PROTECT,
        related_name="payments",
    )
    payment_date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    payment_method = models.CharField(max_length=30, choices=PAYMENT_METHOD_CHOICES, default="bank_transfer")
    reference = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="egg_pos_commission_payments_recorded",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["payment_date", "id"]

    def clean(self):
        super().clean()
        if self.amount is None or self.amount <= MONEY_ZERO:
            raise ValidationError({"amount": "Payment amount must be greater than zero."})

    def __str__(self):
        return f"{self.commission.salesperson.username} commission payment - Rs {self.amount}"
