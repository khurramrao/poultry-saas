"""Supplier payment method corrections must preserve payables and prevent negative cash."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from api.models.egg_pos import EggPOSSupplier, EggPOSSupplierPayment
from api.models.egg_pos_supplier_payment_audit import EggPOSSupplierPaymentMethodAudit
from api.services.accounting import line, money, normal_account_balance, post_entry, supplier_balance


class SupplierPaymentCorrectionTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(
            username="supplier-correction-admin", password="StrongTestPassword-123", is_staff=True
        )
        self.supplier = EggPOSSupplier.objects.create(name="Test Supplier for Correction")
        self.url = reverse("egg_pos_supplier_detail", kwargs={"supplier_id": self.supplier.id})
        self.client.force_login(self.admin)
        today = timezone.localdate()
        post_entry(
            source_key="test:supplier:opening_payable", entry_date=today,
            lines=[
                line("1200", debit=Decimal("17500")),
                line("2000", credit=Decimal("17500"), party_type="supplier",
                     party_id=self.supplier.id, party_name=self.supplier.name),
            ],
        )
        post_entry(
            source_key="test:supplier:opening_bank_cash", entry_date=today,
            lines=[
                line("1000", debit=Decimal("1000")),
                line("1010", debit=Decimal("30000")),
                line("3000", credit=Decimal("31000")),
            ],
        )

    def _post_payment(self, method, amount="17500"):
        return self.client.post(self.url, data={
            "action": "record_payment", "payment_date": timezone.localdate().isoformat(),
            "amount": amount, "payment_method": method, "reference": "ACTUAL-TEST-001",
        })

    def _seed_cash(self, amount="20000"):
        post_entry(source_key="test:supplier:extra_cash", entry_date=timezone.localdate(), lines=[
            line("1000", debit=Decimal(amount)),
            line("3000", credit=Decimal(amount)),
        ])

    def _correct(self, payment, method="bank_transfer", *, password="StrongTestPassword-123", reason="Actual bank transfer confirmed on statement"):
        return self.client.post(self.url, data={
            "action": "correct_payment_method", "payment_id": str(payment.id),
            "new_method": method, "admin_password": password, "reason": reason,
        })

    def test_cash_payment_rejected_without_available_funds(self):
        response = self._post_payment("cash")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(EggPOSSupplierPayment.objects.count(), 0)
        self.assertEqual(normal_account_balance("1000"), Decimal("1000.00"))
        self.assertEqual(supplier_balance(self.supplier), Decimal("17500.00"))

    def test_blank_method_is_rejected(self):
        response = self._post_payment("")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(EggPOSSupplierPayment.objects.exists())

    def test_bank_payment_records_bank_only(self):
        response = self._post_payment("bank_transfer")
        self.assertEqual(response.status_code, 302)
        payment = EggPOSSupplierPayment.objects.get()
        self.assertEqual(payment.payment_method, "bank_transfer")
        self.assertEqual(normal_account_balance("1000"), Decimal("1000.00"))
        self.assertEqual(normal_account_balance("1010"), Decimal("12500.00"))
        self.assertEqual(supplier_balance(self.supplier), Decimal("0.00"))

    def test_correct_cash_to_bank_without_duplicate_supplier_payment(self):
        self._seed_cash()
        original = self._post_payment("cash")
        self.assertEqual(original.status_code, 302)
        payment = EggPOSSupplierPayment.objects.get()
        self.assertEqual(normal_account_balance("1000"), Decimal("3500.00"))
        self.assertEqual(normal_account_balance("1010"), Decimal("30000.00"))
        self.assertEqual(supplier_balance(self.supplier), Decimal("0.00"))
        corrected = self._correct(payment)
        self.assertEqual(corrected.status_code, 302)
        payment.refresh_from_db()
        self.assertEqual(payment.payment_method, "bank_transfer")
        self.assertEqual(EggPOSSupplierPayment.objects.count(), 1)
        self.assertEqual(normal_account_balance("1000"), Decimal("21000.00"))
        self.assertEqual(normal_account_balance("1010"), Decimal("12500.00"))
        self.assertEqual(supplier_balance(self.supplier), Decimal("0.00"))
        audit = EggPOSSupplierPaymentMethodAudit.objects.get()
        self.assertEqual(audit.old_method, "cash")
        self.assertEqual(audit.new_method, "bank_transfer")
        self.assertEqual(audit.corrected_by_id, self.admin.pk)

    def test_admin_password_required_and_no_partial_correction(self):
        self._seed_cash()
        self._post_payment("cash")
        payment = EggPOSSupplierPayment.objects.get()
        response = self._correct(payment, password="wrong")
        self.assertEqual(response.status_code, 302)
        payment.refresh_from_db()
        self.assertEqual(payment.payment_method, "cash")
        self.assertFalse(EggPOSSupplierPaymentMethodAudit.objects.exists())
        self.assertEqual(normal_account_balance("1000"), Decimal("3500.00"))

    def test_no_second_correction_to_same_method(self):
        self._seed_cash()
        self._post_payment("cash")
        payment = EggPOSSupplierPayment.objects.get()
        self._correct(payment)
        response = self._correct(payment)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(EggPOSSupplierPaymentMethodAudit.objects.count(), 1)

    def test_correction_rejected_when_bank_had_insufficient_funds(self):
        self._seed_cash()
        post_entry(source_key="test:supplier:bank_depletion", entry_date=timezone.localdate(), lines=[
            line("3000", debit=Decimal("25000")), line("1010", credit=Decimal("25000")),
        ])
        self._post_payment("cash")
        payment = EggPOSSupplierPayment.objects.get()
        result = self._correct(payment)
        self.assertEqual(result.status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.payment_method, "cash")
        self.assertEqual(supplier_balance(self.supplier), Decimal("0.00"))
        self.assertFalse(EggPOSSupplierPaymentMethodAudit.objects.exists())
