"""Run: python manage.py test api.admin_expense_new_categories_tests"""
from datetime import date
from decimal import Decimal
from uuid import uuid4
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from api.models.egg_pos_admin_expense import EggPOSAdminExpense
from api.models.accounting import JournalEntry
from api.services.accounting import income_statement


class NewAdminExpenseCategoriesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(username='expense_categories_admin', password='Verify123!!!', email='admin@example.com')

    def setUp(self):
        self.client.force_login(self.user)

    def test_three_expense_categories_and_distinct_journals(self):
        choices = dict(EggPOSAdminExpense.CATEGORY_CHOICES)
        for category, expected_account in [('packing_material','6130'), ('stock_transport','6160'), ('social_media_marketing','6365')]:
            self.assertIn(category, choices)
            response = self.client.post(reverse('egg_pos_admin_expenses'), {
                'expense_date':'2026-11-20','category': category, 'amount':'150.00',
                'payment_source':'cash','paid_to':'Example Supplier','reference':f'{category}-001',
                'notes':'Test expense', 'admin_password':'Verify123!!!', 'request_token':str(uuid4()),
            })
            self.assertEqual(response.status_code, 302, response.content[:400])
            expense = EggPOSAdminExpense.objects.get(category=category)
            entry = JournalEntry.objects.get(source_key=f'egg_pos:admin_expense:{expense.pk}')
            account_codes = set(entry.lines.values_list('account__code', flat=True))
            self.assertEqual(account_codes, {expected_account,'1000'})
        pnl = income_statement(date(2026,11,1),date(2026,11,30))
        self.assertEqual(pnl['expenses'], Decimal('450.00'))
