"""Run: python manage.py test api.retail_delivery_expense_tests api.admin_expense_tests"""
import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from api.models.accounting import JournalEntry
from api.models.egg_pos_admin_expense import EggPOSAdminExpense
from api.services.accounting import income_statement


class RetailDeliveryExpenseTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            username="delivery_administrator", email="admin@example.net", password="TestDelivery!2026")
        self.client.force_login(self.admin)

    def _post(self, payment="cash", token=None):
        return self.client.post(reverse("egg_pos_admin_expenses"), {
            "expense_date": "2026-11-11", "category": "delivery", "amount": "180.00",
            "payment_source": payment, "paid_to": "Courier Test", "reference": "RNE-TEST-135",
            "notes": "Customer delivery paid to courier", "admin_password": "TestDelivery!2026",
            "request_token": str(token or uuid.uuid4()),
        })

    def test_delivery_posts_to_selling_expense_not_inventory_or_cogs(self):
        self.assertEqual(self._post().status_code, 302)
        row = EggPOSAdminExpense.objects.get()
        self.assertEqual(row.get_category_display(), "Customer Delivery / Courier Charges")
        journal = JournalEntry.objects.get(source_key=f"egg_pos:admin_expense:{row.pk}")
        self.assertEqual({(line.account.code, line.debit, line.credit) for line in journal.lines.all()}, {
            ("6110", Decimal("180.00"), Decimal("0.00")),
            ("1000", Decimal("0.00"), Decimal("180.00")),
        })
        profit = income_statement(__import__("datetime").date(2026, 11, 1), __import__("datetime").date(2026, 11, 30))
        self.assertEqual(profit["expenses"], Decimal("180.00"))

    def test_delivery_bank_payment_and_idempotency(self):
        token = uuid.uuid4()
        self.assertEqual(self._post("bank", token).status_code, 302)
        self.assertEqual(self._post("bank", token).status_code, 302)
        self.assertEqual(EggPOSAdminExpense.objects.count(), 1)
        entry = JournalEntry.objects.get(source_type="egg_pos_admin_expense")
        self.assertEqual(set(entry.lines.values_list("account__code", flat=True)), {"6110", "1010"})

    def test_existing_purchase_transport_is_capitalized_not_admin_expense(self):
        from api.services.egg_pos_accounting import sync_purchase
        import inspect
        source = inspect.getsource(sync_purchase)
        self.assertIn('line("1200", debit=landed', source)
        self.assertIn('transport_payment_method', source)
