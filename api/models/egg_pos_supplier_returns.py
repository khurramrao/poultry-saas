"""Physical supplier returns, separate from writeoffs and purchase corrections."""
from decimal import Decimal
from uuid import uuid4
from django.conf import settings
from django.db import models
from django.utils import timezone

class EggPOSSupplierReturn(models.Model):
    SETTLEMENT = [
        ("credit_note", "Supplier credit note (reduce payable)"),
        ("refund_due", "Refund approved — awaiting payment"),
    ]
    REASONS = [
        ("quality", "Quality / unsuitable grade"),
        ("damaged_on_arrival", "Damaged on arrival"),
        ("wrong_item", "Incorrect product / size"),
        ("excess", "Excess purchase"),
        ("other", "Other agreed supplier return"),
    ]
    token = models.UUIDField(unique=True, default=uuid4, editable=False)
    lot = models.ForeignKey("api.EggPOSInventoryLot", related_name="supplier_returns", on_delete=models.PROTECT)
    purchase = models.ForeignKey("api.EggPOSPurchase", related_name="supplier_returns", on_delete=models.PROTECT)
    supplier = models.ForeignKey("api.EggPOSSupplier", related_name="retail_returns", on_delete=models.PROTECT)
    return_date = models.DateField(default=timezone.localdate)
    quantity = models.PositiveIntegerField()
    unit_cost = models.DecimalField(max_digits=14, decimal_places=6)
    inventory_value = models.DecimalField(max_digits=14, decimal_places=2)
    supplier_credit = models.DecimalField(max_digits=14, decimal_places=2)
    settlement = models.CharField(max_length=20, choices=SETTLEMENT)
    reason = models.CharField(max_length=30, choices=REASONS)
    supplier_reference = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="retail_supplier_returns_created")
    created_at = models.DateTimeField(auto_now_add=True)
    is_voided = models.BooleanField(default=False)
    void_reason = models.CharField(max_length=500, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="retail_supplier_returns_voided")

    class Meta:
        ordering = ["-created_at", "-id"]

    @property
    def reference(self):
        return f"RNRET-{self.pk:06d}" if self.pk else "RNRET-NEW"

    @property
    def refund_remaining(self):
        if self.settlement != "refund_due" or self.is_voided:
            return Decimal("0.00")
        return max(Decimal("0.00"), self.supplier_credit - sum((x.amount for x in self.refunds.filter(is_voided=False)), Decimal("0.00")))

class EggPOSSupplierReturnRefund(models.Model):
    PAYMENT = [("cash", "Main Cash"), ("bank_transfer", "Bank / Digital")]
    token = models.UUIDField(unique=True, default=uuid4, editable=False)
    supplier_return = models.ForeignKey(EggPOSSupplierReturn, related_name="refunds", on_delete=models.PROTECT)
    refund_date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=PAYMENT)
    bank_reference = models.CharField(max_length=120, blank=True)
    notes = models.CharField(max_length=500, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="retail_return_refunds_recorded")
    created_at = models.DateTimeField(auto_now_add=True)
    is_voided = models.BooleanField(default=False)
    void_reason = models.CharField(max_length=500, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="retail_return_refunds_voided")

    class Meta:
        ordering = ["-created_at", "-id"]
