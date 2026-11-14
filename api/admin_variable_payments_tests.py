"""Tests for two separate superuser payouts, ledger classification and guards."""
from datetime import date
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from api.models.accounting import JournalEntry
from api.models.egg_pos_payroll import EggPOSAdminVariablePayment, EggPOSSalaryPlan
from api.services.accounting import line, post_entry, normal_account_balance
from api.services.egg_pos_payroll import record_admin_variable_payment


class AdminVariablePaymentsTests(TestCase):
    def setUp(self):
        U = get_user_model()
        self.admin = U.objects.create_superuser(username='admin_test_payout', password='S0meSecret!')
        self.iqra = U.objects.create_superuser(username='iqramanzoor_test_payout', password='AnotherSecret!')
        self.staff = U.objects.create_user(username='worker', password='workerPwd!')
        post_entry(
            source_key='test:funds:owner', entry_date=date(2026, 11, 1),
            reference='Opening balance', created_by=self.admin,
            lines=[line('1000', debit=Decimal('30000')), line('3000', credit=Decimal('30000'))],
        )
        post_entry(
            source_key='test:funds:bank', entry_date=date(2026, 11, 1),
            reference='Opening bank balance', created_by=self.admin,
            lines=[line('1010', debit=Decimal('40000')), line('3000', credit=Decimal('40000'))],
        )

    def pay(self, person, amount, source='cash', kind='salary', token=None):
        with patch('api.services.egg_pos_payroll.timezone.localdate', return_value=date(2026, 11, 14)):
            return record_admin_variable_payment(
                recipient_id=person.pk, amount=Decimal(amount), paid_on=date(2026, 11, 13),
                kind=kind, payment_source=source, recorded_by=self.admin,
                request_token=token or uuid4(), notes='Owner income withdrawal',
            )

    def test_admin_and_iqra_payment_ledger_separate_without_monthly_plan(self):
        a, duplicate_a = self.pay(self.admin, '10000.00', 'cash')
        i, duplicate_i = self.pay(self.iqra, '15000.00', 'bank')
        self.assertFalse(duplicate_a)
        self.assertFalse(duplicate_i)
        self.assertEqual(normal_account_balance('1000'), Decimal('20000.00'))
        self.assertEqual(normal_account_balance('1010'), Decimal('25000.00'))
        self.assertEqual(normal_account_balance('6390'), Decimal('25000.00'))
        self.assertFalse(EggPOSSalaryPlan.objects.exists())
        rows_a = list(JournalEntry.objects.get(source_key=f'egg_pos:admin_variable_payment:{a.pk}').lines.all())
        rows_i = list(JournalEntry.objects.get(source_key=f'egg_pos:admin_variable_payment:{i.pk}').lines.all())
        self.assertEqual({r.party_id for r in rows_a}, {self.admin.id})
        self.assertEqual({r.party_id for r in rows_i}, {self.iqra.id})
        self.client.force_login(self.admin)
        response = self.client.get(reverse('egg_pos_payroll'))
        self.assertContains(response, 'Variable Admin Payments')
        self.assertContains(response, 'iqramanzoor_test_payout')
        self.assertContains(response, '10,000')
        self.assertContains(response, '15,000')

    def test_owner_drawing_does_not_increase_salary_expense(self):
        self.pay(self.iqra, '5000.00', 'bank', kind='drawing')
        self.assertEqual(normal_account_balance('6390'), Decimal('0.00'))
        self.assertEqual(normal_account_balance('1010'), Decimal('35000.00'))
        self.assertEqual(normal_account_balance('3000'), Decimal('65000.00'))

    def test_duplicate_token_and_insufficient_cash(self):
        token = uuid4()
        self.pay(self.admin, '5000', token=token)
        _, repeated = self.pay(self.admin, '5000', token=token)
        self.assertTrue(repeated)
        self.assertEqual(EggPOSAdminVariablePayment.objects.count(), 1)
        with self.assertRaisesMessage(ValueError, 'Insufficient funds'):
            self.pay(self.admin, '26000')
        self.assertEqual(EggPOSAdminVariablePayment.objects.count(), 1)

    def test_non_admin_recipient_is_rejected(self):
        with self.assertRaisesMessage(ValueError, 'active administrator'):
            self.pay(self.staff, '1000')

    def test_screen_requires_password_and_requires_superuser_for_payment(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('egg_pos_payroll'), {
            'action': 'admin_variable_payment', 'recipient': self.iqra.pk,
            'amount': '1000', 'kind': 'salary', 'paid_on': '2026-11-13',
            'payment_source': 'cash', 'request_token': str(uuid4()),
            'admin_password': 'incorrect',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(EggPOSAdminVariablePayment.objects.count(), 0)
        with patch('api.views.egg_pos_payroll.timezone.localdate', return_value=date(2026, 11, 14)):
            resp = self.client.post(reverse('egg_pos_payroll'), {
                'action': 'admin_variable_payment', 'recipient': self.iqra.pk,
                'amount': '1000', 'kind': 'salary', 'paid_on': '2026-11-13',
                'payment_source': 'cash', 'request_token': str(uuid4()),
                'admin_password': 'S0meSecret!',
            })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(EggPOSAdminVariablePayment.objects.count(), 1)

    def test_choose_correct_employee_for_november_transition(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('egg_pos_payroll')+'?salary_employee='+str(self.staff.id))
        self.assertContains(resp, 'id="salary-plan-section"')
        self.assertContains(resp, 'selected')
