"""python manage.py test api.purchase_void_tests"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from api.models.accounting import JournalEntry
from api.models.egg_pos import (
    EggPOSInventoryLot, EggPOSProduct, EggPOSPurchase, EggPOSPurchaseItem,
    EggPOSSupplier, EggPOSSupplierPayment,
)
from api.services.egg_pos_accounting import sync_purchase, sync_all_egg_pos_books
from api.services.egg_pos_purchase_void import void_supplier_purchase
from api.services.accounting import raw_account_balance


class PurchaseDuplicateVoidTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(
            username="void_owner", email="void@example.test", password="secret-void-123")
        cls.salesperson = get_user_model().objects.create_user(username="void_staff", password="staff-void-123")
        cls.product = EggPOSProduct.objects.create(name="Void eggs", sku="VOID-EGGS")
        cls.supplier = EggPOSSupplier.objects.create(name="Void supplier")

    def make_purchase(self, suffix="1", transport=Decimal("0.00")):
        purchase = EggPOSPurchase.objects.create(
            supplier=self.supplier, purchase_date="2026-10-25",
            transport_cost=transport, transport_payment_method="cash",
            reference=f"DUP-{suffix}", created_by=self.admin,
        )
        lot = EggPOSInventoryLot.objects.create(
            product=self.product, supplier=self.supplier, purchase=purchase,
            source_type="purchase", lot_code=f"VOID-LOT-{suffix}",
            received_date="2026-10-25", quantity_received=1080,
            quantity_remaining=1080, unit_cost=Decimal("18.333333"),
        )
        EggPOSPurchaseItem.objects.create(
            purchase=purchase, product=self.product, lot=lot,
            quantity=1080, unit_cost=Decimal("18.333333"),
        )
        sync_purchase(purchase)
        return purchase, lot

    def test_void_removes_only_unused_stock_and_reverses_original_journal(self):
        purchase, lot = self.make_purchase(transport=Decimal("100.00"))
        old_ledger_balance = raw_account_balance("2000")
        self.assertNotEqual(old_ledger_balance, Decimal("0.00"))
        self.client.force_login(self.admin)
        response = self.client.post(reverse("egg_pos_void_purchase", args=[purchase.pk]), {
            "admin_password": "secret-void-123",
            "reason": "Duplicate purchase accidentally entered",
        })
        self.assertEqual(response.status_code, 302)
        purchase.refresh_from_db()
        self.assertTrue(purchase.is_voided)
        self.assertEqual(purchase.balance_due, Decimal("0.00"))
        self.assertFalse(EggPOSInventoryLot.objects.filter(pk=lot.pk).exists())
        self.assertEqual(purchase.items.count(), 1)
        self.assertIsNone(purchase.items.first().lot_id)
        self.assertEqual(raw_account_balance("2000"), Decimal("0.00"))
        self.assertEqual(raw_account_balance("1200"), Decimal("0.00"))
        self.assertEqual(raw_account_balance("1000"), Decimal("0.00"))
        self.assertEqual(JournalEntry.objects.filter(source_key=f"egg_pos:purchase:{purchase.pk}").count(), 1)
        self.assertEqual(JournalEntry.objects.filter(source_key=f"egg_pos:purchase:void:{purchase.pk}").count(), 1)
        sync_all_egg_pos_books()
        self.assertEqual(raw_account_balance("2000"), Decimal("0.00"))

    def test_paid_purchase_cannot_be_voided(self):
        purchase, lot = self.make_purchase(suffix="2")
        EggPOSSupplierPayment.objects.create(
            supplier=self.supplier, purchase=purchase, payment_date="2026-10-25",
            amount=Decimal("100.00"), payment_method="cash", recorded_by=self.admin,
        )
        with self.assertRaisesMessage(ValueError, "supplier payments"):
            void_supplier_purchase(purchase_id=purchase.pk, actor=self.admin, reason="Duplicate entered by mistake")
        purchase.refresh_from_db()
        self.assertFalse(purchase.is_voided)
        self.assertTrue(EggPOSInventoryLot.objects.filter(pk=lot.pk).exists())

    def test_partially_consumed_purchase_cannot_be_voided(self):
        purchase, lot = self.make_purchase(suffix="3")
        lot.quantity_remaining = 1000
        lot.save(update_fields=["quantity_remaining"])
        with self.assertRaisesMessage(ValueError, "Some eggs were sold"):
            void_supplier_purchase(purchase_id=purchase.pk, actor=self.admin, reason="Duplicate purchase please cancel")
        self.assertFalse(EggPOSPurchase.objects.get(pk=purchase.pk).is_voided)

    def test_second_void_is_blocked_and_original_kept(self):
        purchase, _ = self.make_purchase(suffix="4")
        void_supplier_purchase(purchase_id=purchase.pk, actor=self.admin, reason="Purchase accidentally duplicated")
        with self.assertRaisesMessage(ValueError, "already been voided"):
            void_supplier_purchase(purchase_id=purchase.pk, actor=self.admin, reason="Purchase accidentally duplicated")
        self.assertEqual(JournalEntry.objects.filter(source_key=f"egg_pos:purchase:void:{purchase.pk}").count(), 1)

    def test_wrong_password_and_non_admin_leave_stock_untouched(self):
        purchase, lot = self.make_purchase(suffix="5")
        url = reverse("egg_pos_void_purchase", args=[purchase.pk])
        self.client.force_login(self.admin)
        resp = self.client.post(url, {"admin_password": "wrong", "reason": "Duplicate purchase entered"})
        self.assertEqual(resp.status_code, 302)
        self.client.force_login(self.salesperson)
        resp = self.client.post(url, {"admin_password": "staff-void-123", "reason": "Duplicate purchase entered"})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(EggPOSPurchase.objects.get(pk=purchase.pk).is_voided)
        self.assertTrue(EggPOSInventoryLot.objects.filter(pk=lot.pk).exists())
