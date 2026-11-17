"""Auditable reclassification of an existing purchased egg lot to another SKU.

No supplier invoice, cash payment, or second purchase is created.
"""
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from api.models.egg_pos import EggPOSInventoryLot, EggPOSProduct


class EggPOSSkuTransfer(models.Model):
    source_lot = models.ForeignKey(
        EggPOSInventoryLot, on_delete=models.PROTECT, related_name="sku_transfers_out"
    )
    destination_lot = models.OneToOneField(
        EggPOSInventoryLot, on_delete=models.PROTECT, related_name="sku_transfer_in"
    )
    source_product = models.ForeignKey(
        EggPOSProduct, on_delete=models.PROTECT, related_name="sku_transfers_out"
    )
    destination_product = models.ForeignKey(
        EggPOSProduct, on_delete=models.PROTECT, related_name="sku_transfers_in"
    )
    quantity = models.PositiveIntegerField()
    # Snapshot at posting, for the audit trail; the destination lot itself
    # carries the current landed FIFO cost after any later vendor correction.
    unit_cost_at_transfer = models.DecimalField(max_digits=14, decimal_places=6)
    reason = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="egg_pos_sku_transfers_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    is_reversed = models.BooleanField(default=False)
    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="egg_pos_sku_transfers_reversed",
    )
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversal_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def clean(self):
        if self.quantity is None or self.quantity <= 0:
            raise ValidationError({"quantity": "Enter a positive quantity."})
        if self.source_product_id and self.destination_product_id == self.source_product_id:
            raise ValidationError({"destination_product": "Choose a different SKU."})
        if self.unit_cost_at_transfer is not None and Decimal(self.unit_cost_at_transfer) < 0:
            raise ValidationError({"unit_cost_at_transfer": "FIFO cost cannot be negative."})

    @property
    def reference(self):
        return f"RNSKU-{self.pk:06d}" if self.pk else "RNSKU-NEW"

    def __str__(self):
        return f"{self.reference}: {self.quantity} {self.source_product.sku} -> {self.destination_product.sku}"
