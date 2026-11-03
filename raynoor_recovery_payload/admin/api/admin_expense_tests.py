"""Run with: python manage.py test api.admin_expense_tests"""
import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from api.models.accounting import JournalEntry, JournalLine
from api.models.egg_pos_admin_expense import EggPOSAdminExpense
from api.services.accounting import income_statement, raw_account_balance
from api.services.egg_pos_admin_expenses import rebuild_admin_expenses


class AdminExpenseTests(TestCase):
    def setUp(self):
        U = get_user_model()
        self.admin = U.objects.create_superuser(username="expense_admin", email="admin@example.org", password="Zebra!2026Admin")
        self.staff = U.objects.create_user(username="ordinary_employee", password="Zebra!2026Staff")
        self.client.force_login(self.admin)
        self.url = reverse("egg_pos_admin_expenses")

    def _post(self, *, category="rent", amount="5000.00", payment="cash", token=None, password="Zebra!2026Admin"):
        return self.client.post(self.url, {
            "expense_date": "2026-10-20", "category": category, "amount": amount,
            "payment_source": payment, "paid_to": "Landlord",
            "reference": "TEST-RENT", "notes": "Test expense",
            "admin_password": password, "request_token": str(token or uuid.uuid4()),
        })

    def test_admin_rent_posts_expense_and_cashbook_without_commission(self):
        response = self._post()
        self.assertEqual(response.status_code, 302)
        expense = EggPOSAdminExpense.objects.get()
        entry = JournalEntry.objects.get(source_key=f"egg_pos:admin_expense:{expense.pk}")
        self.assertEqual(entry.source_type, "egg_pos_admin_expense")
        self.assertEqual({(l.account.code, l.debit, l.credit) for l in entry.lines.all()}, {
            ("6300", Decimal("5000.00"), Decimal("0.00")),
            ("1000", Decimal("0.00"), Decimal("5000.00")),
        })
        self.assertEqual(raw_account_balance("1000"), Decimal("-5000.00"))
        self.assertFalse(JournalEntry.objects.filter(source_type="egg_pos_commission_period").exists())
        pl = income_statement(__import__("datetime").date(2026, 10, 1), __import__("datetime").date(2026, 10, 31))
        self.assertEqual(pl["expenses"], Decimal("5000.00"))

    def test_equipment_is_fixed_asset_not_income_statement_expense(self):
        self._post(category="equipment", amount="20000.00", payment="bank")
        entry = JournalEntry.objects.get(source_type="egg_pos_admin_expense")
        codes = set(entry.lines.values_list("account__code", flat=True))
        self.assertEqual(codes, {"1500", "1010"})
        pl = income_statement(__import__("datetime").date(2026, 10, 1), __import__("datetime").date(2026, 10, 31))
        self.assertEqual(pl["expenses"], Decimal("0.00"))

    def test_double_submit_is_idempotent(self):
        token = uuid.uuid4()
        self._post(token=token)
        self._post(token=token)
        self.assertEqual(EggPOSAdminExpense.objects.count(), 1)
        self.assertEqual(JournalEntry.objects.filter(source_type="egg_pos_admin_expense").count(), 1)

    def test_void_posts_audit_reversal_not_delete(self):
        self._post()
        e = EggPOSAdminExpense.objects.get()
        response = self.client.post(reverse("egg_pos_void_admin_expense", args=[e.id]), {
            "reason": "Duplicate entered by mistake", "admin_password": "Zebra!2026Admin",
        })
        self.assertEqual(response.status_code, 302)
        e.refresh_from_db()
        self.assertTrue(e.is_voided)
        self.assertEqual(JournalEntry.objects.filter(source_id=e.id, source_type__in=["egg_pos_admin_expense", "egg_pos_admin_expense_void"]).count(), 2)
        self.assertEqual(raw_account_balance("1000"), Decimal("0.00"))
        self.client.post(reverse("egg_pos_void_admin_expense", args=[e.id]), {
            "reason": "Accidentally submitted twice", "admin_password": "Zebra!2026Admin",
        })
        self.assertEqual(JournalEntry.objects.filter(source_type="egg_pos_admin_expense_void").count(), 1)

    def test_wrong_password_or_unprivileged_user_cannot_post(self):
        self._post(password="wrong")
        self.assertFalse(EggPOSAdminExpense.objects.exists())
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self._post().status_code, 403)
        self.assertFalse(EggPOSAdminExpense.objects.exists())

    def test_rebuild_remains_idempotent(self):
        self._post()
        e = EggPOSAdminExpense.objects.get()
        self.client.post(reverse("egg_pos_void_admin_expense", args=[e.id]), {
            "reason": "Duplicate entered by mistake", "admin_password": "Zebra!2026Admin",
        })
        for _ in range(2):
            rebuild_admin_expenses()
        self.assertEqual(JournalEntry.objects.filter(source_id=e.id, source_type__in=["egg_pos_admin_expense", "egg_pos_admin_expense_void"]).count(), 2)
        self.assertEqual(raw_account_balance("1000"), Decimal("0.00"))
