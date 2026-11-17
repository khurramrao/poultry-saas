"""python manage.py test api.sku_transfer_tests"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from api.models.egg_pos import (
    EggPOSProduct, EggPOSSupplier, EggPOSPurchase, EggPOSPurchaseItem,
    EggPOSInventoryLot,
)
from api.models.egg_pos_sku_transfer import EggPOSSkuTransfer
from api.services.egg_pos_sku_transfer import transfer_purchase_lot, reverse_sku_transfer


class PurchaseSkuTransferTests(TestCase):
    def setUp(self):
        U = get_user_model()
        self.admin = U.objects.create_superuser("sku_admin", "sku@example.invalid", "pass123")
        self.employee = U.objects.create_user("sku_employee", "staff@example.invalid", "pass123")
        self.a = EggPOSProduct.objects.create(name="Test White Egg 607", sku="TEST-W607", category="egg")
        self.b = EggPOSProduct.objects.create(name="Test Premium White Egg", sku="TEST-PREM", category="egg")
        self.supplier = EggPOSSupplier.objects.create(name="Test SKU Supplier")
        self.purchase = EggPOSPurchase.objects.create(supplier=self.supplier, created_by=self.admin)
        self.lot = EggPOSInventoryLot.objects.create(
            product=self.a, source_type="purchase", purchase=self.purchase,
            supplier=self.supplier, lot_code="SKU-TEST-ORIGINAL", quantity_received=720,
            quantity_remaining=720, unit_cost=Decimal("21.666667"),
        )
        self.item = EggPOSPurchaseItem.objects.create(
            purchase=self.purchase, product=self.a, quantity=720,
            unit_cost=Decimal("21.666667"), lot=self.lot,
        )

    def post_movement(self, quantity=120):
        return transfer_purchase_lot(
            purchase=self.purchase, source_lot_id=self.lot.id,
            destination_product_id=self.b.id, quantity=quantity,
            reason="Regraded by physical egg quality", actor=self.admin,
        )

    def test_transfer_keeps_purchased_units_and_value(self):
        before = self.lot.stock_value
        transfer = self.post_movement()
        self.lot.refresh_from_db()
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 720)
        self.assertEqual(self.lot.quantity_received, 720)
        self.assertEqual(self.lot.quantity_remaining, 600)
        self.assertEqual(transfer.destination_lot.quantity_remaining, 120)
        self.assertEqual(transfer.destination_lot.product, self.b)
        self.assertEqual(transfer.destination_lot.purchase, self.purchase)
        self.assertEqual(self.lot.stock_value + transfer.destination_lot.stock_value, before)
        self.assertEqual(EggPOSPurchase.objects.count(), 1)
        self.assertEqual(EggPOSPurchaseItem.objects.count(), 1)

    def test_excess_stock_rejected_without_partial_changes(self):
        with self.assertRaisesRegex(ValueError, "physical units remain"):
            self.post_movement(quantity=721)
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantity_remaining, 720)
        self.assertFalse(EggPOSSkuTransfer.objects.exists())

    def test_non_admin_rejected(self):
        with self.assertRaisesRegex(ValueError, "administrator"):
            transfer_purchase_lot(purchase=self.purchase, source_lot_id=self.lot.pk,
                destination_product_id=self.b.pk, quantity=10, actor=self.employee,
                reason="Regraded by physical egg quality")
        self.assertFalse(EggPOSSkuTransfer.objects.exists())

    def test_reversal_restores_stock_and_keeps_history(self):
        movement = self.post_movement()
        reverse_sku_transfer(transfer_id=movement.pk, actor=self.admin,
                             reason="Mistaken SKU reclassification")
        movement.refresh_from_db()
        self.lot.refresh_from_db()
        movement.destination_lot.refresh_from_db()
        self.assertTrue(movement.is_reversed)
        self.assertEqual(self.lot.quantity_remaining, 720)
        self.assertEqual(movement.destination_lot.quantity_remaining, 0)
        self.assertTrue(EggPOSSkuTransfer.objects.filter(pk=movement.pk).exists())

    def test_reversal_rejected_after_destination_usage(self):
        movement = self.post_movement()
        dest_lot = movement.destination_lot
        dest_lot.quantity_remaining = 119
        dest_lot.save(update_fields=["quantity_remaining"])
        with self.assertRaisesRegex(ValueError, "has been used"):
            reverse_sku_transfer(transfer_id=movement.pk, actor=self.admin,
                                 reason="Attempted reversal")
        movement.refresh_from_db()
        self.assertFalse(movement.is_reversed)

    def test_admin_password_required_in_view(self):
        self.client.force_login(self.admin)
        url = reverse("egg_pos_sku_transfer", args=[self.purchase.pk])
        response = self.client.post(url, {
            "source_lot_id": self.lot.pk,
            "destination_product_id": self.b.pk,
            "quantity": 120,
            "reason": "Regraded by physical egg quality",
            "admin_password": "wrong-password",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(EggPOSSkuTransfer.objects.exists())
