"""Run with: python manage.py test api.supplier_purchase_returns_tests"""
from decimal import Decimal
from uuid import uuid4
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from api.models.egg_pos import EggPOSProduct, EggPOSSupplier, EggPOSPurchase, EggPOSPurchaseItem, EggPOSInventoryLot
from api.models.egg_pos_supplier_returns import EggPOSSupplierReturn
from api.services.egg_pos_supplier_returns import record_return, record_refund, reverse_refund, reverse_return
from api.services.accounting import normal_account_balance


class SupplierReturnsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username="return_tester", email="test@example.com", password="a-long-test-password")
        self.supplier = EggPOSSupplier.objects.create(name="Return Test Supplier")
        self.product = EggPOSProduct.objects.create(name="Return Test Eggs", sku="RTN-TEST-45", category="egg")
        self.purchase = EggPOSPurchase.objects.create(supplier=self.supplier, purchase_date=timezone.localdate(), created_by=self.user)
        self.lot = EggPOSInventoryLot.objects.create(
            product=self.product, source_type="purchase", supplier=self.supplier, purchase=self.purchase,
            lot_code="RTN-TEST-LOT", received_date=timezone.localdate(), quantity_received=210,
            quantity_remaining=166, unit_cost=Decimal("28.333333"),
        )
        EggPOSPurchaseItem.objects.create(purchase=self.purchase, product=self.product, quantity=210,
                                          unit_cost=Decimal("28.000000"), lot=self.lot)

    def test_120_return_leaves_46_without_cash_sale_or_wastage(self):
        obj = record_return(lot_id=self.lot.id, quantity=120, supplier_credit="3360.00",
                            settlement="credit_note", reason="quality", actor=self.user,
                            return_date=str(timezone.localdate()), token=str(uuid4()))
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantity_remaining, 46)
        self.assertEqual(obj.inventory_value, Decimal("3400.00"))
        self.assertEqual(obj.supplier_credit, Decimal("3360.00"))
        self.assertEqual(obj.purchase_id, self.purchase.id)
        self.assertEqual(obj.supplier_id, self.supplier.id)
        self.assertEqual(normal_account_balance("2000", party_type="supplier", party_id=self.supplier.id), Decimal("-3360.00"))
        self.assertEqual(normal_account_balance("6160"), Decimal("40.00"))

    def test_refund_pending_and_bank_refund_once(self):
        obj = record_return(lot_id=self.lot.id, quantity=12, supplier_credit="336.00",
                            settlement="refund_due", reason="quality", actor=self.user,
                            return_date=str(timezone.localdate()), token=str(uuid4()))
        self.assertEqual(obj.refund_remaining, Decimal("336.00"))
        payment = record_refund(return_id=obj.id, amount="200.00", payment_method="bank_transfer",
                                refund_date=str(timezone.localdate()), token=str(uuid4()), actor=self.user)
        self.assertEqual(obj.refund_remaining, Decimal("136.00"))
        with self.assertRaisesMessage(ValueError, "only Rs"):
            record_refund(return_id=obj.id, amount="200.00", payment_method="cash",
                          refund_date=str(timezone.localdate()), token=str(uuid4()), actor=self.user)
        reverse_refund(refund_id=payment.pk, actor=self.user, reason="Bank duplicate entry correction")
        self.assertEqual(obj.refund_remaining, Decimal("336.00"))
        reverse_return(return_id=obj.id, actor=self.user, reason="Supplier cancelled approved return")
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantity_remaining, 166)

    def test_no_duplicate_return_token_and_over_return(self):
        token = str(uuid4())
        args=dict(lot_id=self.lot.id, quantity=12, supplier_credit="336.00",
                  settlement="refund_due", reason="quality", actor=self.user,
                  return_date=str(timezone.localdate()), token=token)
        record_return(**args)
        with self.assertRaisesMessage(ValueError, "already saved"):
            record_return(**args)
        with self.assertRaisesMessage(ValueError, "Only 154"):
            record_return(**{**args,"token":str(uuid4()),"quantity":155})
