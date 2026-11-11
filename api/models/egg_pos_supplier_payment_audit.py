"""Immutable audit trail for administrative corrections to supplier payment methods."""
from django.conf import settings
from django.db import models

from api.models.egg_pos import EggPOSSupplierPayment


class EggPOSSupplierPaymentMethodAudit(models.Model):
    payment = models.ForeignKey(EggPOSSupplierPayment, on_delete=models.PROTECT, related_name="method_corrections")
    old_method = models.CharField(max_length=30)
    new_method = models.CharField(max_length=30)
    reason = models.CharField(max_length=500)
    corrected_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="supplier_payment_method_corrections")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"SUPPAY-{self.payment_id:05d}: {self.old_method} -> {self.new_method}"
