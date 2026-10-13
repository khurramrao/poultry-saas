"""Regression tests for the admin cash <-> bank transfer workflow."""
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from api.models.accounting import EggPOSFundsTransfer, JournalEntry
from api.services.accounting import (
    line, normal_account_balance, post_entry, trial_balance, income_statement,
)


class FundTransferTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(
            username='transfer-admin', password='TestingPass!734', is_staff=True
        )
        self.salesperson = get_user_model().objects.create_user(
            username='transfer-seller', password='TestingPass!734', is_staff=False
        )
        post_entry(
            source_key='test:opening_funds', entry_date=timezone.localdate(),
            reference='OPEN', source_type='owner_capital', source_id=100,
            lines=[
                line('1000', debit=Decimal('3690.00')),
                line('1010', debit=Decimal('24352.00')),
                line('3000', credit=Decimal('28042.00')),
            ],
        )
        self.url = reverse('egg_pos_transfer_funds')
        self.client.force_login(self.admin)

    def transfer(self, amount='3000', direction='cash_to_bank', token=None, password='TestingPass!734'):
        return self.client.post(self.url, {
            'action': 'create',
            'direction': direction,
            'amount': amount,
            'admin_password': password,
            'reference': 'Bank deposit slip 1122',
            'notes': 'Test transfer',
            'request_token': str(token or uuid4()),
        }, follow=True)

    def test_cash_to_bank_changes_each_balance_not_combined_cash_or_profit(self):
        opening = Decimal('28042.00')
        statement_before = income_statement(timezone.localdate(), timezone.localdate())
        response = self.transfer()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(EggPOSFundsTransfer.objects.count(), 1)
        self.assertEqual(normal_account_balance('1000'), Decimal('690.00'))
        self.assertEqual(normal_account_balance('1010'), Decimal('27352.00'))
        self.assertEqual(normal_account_balance('1000') + normal_account_balance('1010'), opening)
        self.assertEqual(income_statement(timezone.localdate(), timezone.localdate()), statement_before)
        entry = JournalEntry.objects.get(source_key=f'egg_pos:fund_transfer:{EggPOSFundsTransfer.objects.get().id}')
        self.assertEqual(entry.total_debit, Decimal('3000.00'))
        self.assertEqual(entry.total_credit, Decimal('3000.00'))
        self.assertContains(response, 'FT-000001')
        self.assertContains(response, '27,352.00')
        balances, debit_total, credit_total = trial_balance(timezone.localdate())
        self.assertEqual(debit_total, credit_total)

    def test_bank_to_cash_and_void_reverses_both_accounts(self):
        self.transfer(amount='1000', direction='bank_to_cash')
        t = EggPOSFundsTransfer.objects.get()
        self.assertEqual(normal_account_balance('1000'), Decimal('4690.00'))
        self.assertEqual(normal_account_balance('1010'), Decimal('23352.00'))
        response = self.client.post(self.url, {
            'action': 'void', 'transfer_id': t.pk,
            'void_reason': 'Incorrect deposit direction',
            'admin_password': 'TestingPass!734',
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        t.refresh_from_db()
        self.assertTrue(t.is_voided)
        self.assertEqual(t.voided_by, self.admin)
        self.assertEqual(normal_account_balance('1000'), Decimal('3690.00'))
        self.assertEqual(normal_account_balance('1010'), Decimal('24352.00'))
        self.assertFalse(JournalEntry.objects.filter(source_key=f'egg_pos:fund_transfer:{t.pk}').exists())

    def test_rejects_overspend_without_mutating_any_cash(self):
        response = self.transfer(amount='3691')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'only Rs 3,690.00')
        self.assertFalse(EggPOSFundsTransfer.objects.exists())
        self.assertEqual(normal_account_balance('1000'), Decimal('3690.00'))

    def test_wrong_password_rejected(self):
        response = self.transfer(password='wrong')
        self.assertContains(response, 'Incorrect admin password')
        self.assertEqual(EggPOSFundsTransfer.objects.count(), 0)

    def test_duplicate_form_does_not_debit_twice(self):
        token = uuid4()
        self.transfer(amount='3000', token=token)
        response = self.transfer(amount='3000', token=token)
        self.assertContains(response, 'already recorded')
        self.assertEqual(EggPOSFundsTransfer.objects.count(), 1)
        self.assertEqual(normal_account_balance('1000'), Decimal('690.00'))

    def test_salesperson_cannot_access_or_post_transfer(self):
        self.client.force_login(self.salesperson)
        response = self.client.get(self.url, follow=True)
        self.assertNotContains(response, 'Record a confirmed transfer')
        response = self.transfer()
        self.assertFalse(EggPOSFundsTransfer.objects.exists())
        self.assertEqual(normal_account_balance('1000'), Decimal('3690.00'))

    def test_cannot_void_if_receiving_bank_funds_already_spent(self):
        self.transfer(amount='3000')
        t = EggPOSFundsTransfer.objects.get()
        # A valid later expense consumes some bank funds. Reverse would overdraw them.
        post_entry(
            source_key='test:spent_bank', entry_date=timezone.localdate(), reference='SPENT',
            lines=[line('6140', debit=Decimal('25000.00')), line('1010', credit=Decimal('25000.00'))],
        )
        response = self.client.post(self.url, {
            'action':'void', 'transfer_id':t.pk, 'void_reason':'Want to reverse funds',
            'admin_password':'TestingPass!734',
        }, follow=True)
        self.assertContains(response, 'Cannot void')
        t.refresh_from_db()
        self.assertFalse(t.is_voided)
        self.assertTrue(JournalEntry.objects.filter(source_key=f'egg_pos:fund_transfer:{t.pk}').exists())

    def test_accounts_dashboard_links_to_transfer_form(self):
        response = self.client.get(reverse('egg_pos_accounting'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Transfer Funds')
