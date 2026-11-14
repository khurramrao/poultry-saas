"""Run python manage.py test api.nov_salary_transition_tests"""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from api.models.egg_pos import EggPOSCommissionPeriod, EggPOSCommissionPayment, EggPOSUserAccess
from api.models.egg_pos_payroll import EggPOSSalaryPlan
from api.models.accounting import JournalEntry
from api.services.egg_pos_payroll import post_salary, verify_commission_transition


class NovemberSalaryTransitionTests(TestCase):
    def setUp(self):
        U = get_user_model()
        self.admin = U.objects.create_superuser(username='owner_nov', password='Secret!nov2026')
        self.zayyan = U.objects.create_user(username='zayyan_nov')
        self.abbas = U.objects.create_user(username='abbas_nov')
        # can_sell is provided by access, leave the service test independent from UI access.
        self.nov = date(2026, 11, 1)

    def _period(self, user, start, end, amount):
        return EggPOSCommissionPeriod.objects.create(
            salesperson=user, period_start=start, period_end=end,
            commission_amount=Decimal(amount), commission_percent=Decimal('10.00'),
            posted_by=self.admin,
        )

    def test_missing_october_commission_days_blocks_abbas_until_closed(self):
        self._period(self.abbas, date(2026, 10, 21), date(2026, 10, 27), '0.00')
        with self.assertRaisesMessage(ValueError, 'Finalize previous commission through 31 Oct 2026'):
            verify_commission_transition(self.abbas.id, self.nov, Decimal('20000'), Decimal('0'))
        self._period(self.abbas, date(2026, 10, 28), date(2026, 10, 31), '0.00')
        self.assertEqual(verify_commission_transition(self.abbas.id, self.nov, Decimal('20000'), Decimal('0')), [])

    def test_zayyan_paid_commission_needs_explicit_credit(self):
        p = self._period(self.zayyan, date(2026, 10, 28), date(2026, 11, 14), '569.00')
        with self.assertRaisesMessage(ValueError, 'not fully paid'):
            verify_commission_transition(self.zayyan.id, self.nov, Decimal('30000'), Decimal('300'))
        EggPOSCommissionPayment.objects.create(
            commission=p, payment_date=date(2026, 11, 14), amount=Decimal('569.00'),
            recorded_by=self.admin,
        )
        with self.assertRaisesMessage(ValueError, 'Enter the already-paid November'):
            verify_commission_transition(self.zayyan.id, self.nov, Decimal('30000'), Decimal('0'))
        with self.assertRaisesMessage(ValueError, 'exceeds posted'):
            verify_commission_transition(self.zayyan.id, self.nov, Decimal('30000'), Decimal('570'))
        self.assertEqual(verify_commission_transition(self.zayyan.id, self.nov, Decimal('30000'), Decimal('300')), [p])

    def test_credited_commission_reclassifies_without_second_cash_payment(self):
        p = self._period(self.zayyan, date(2026, 10, 28), date(2026, 11, 14), '569.00')
        EggPOSCommissionPayment.objects.create(
            commission=p, payment_date=date(2026, 11, 14), amount=Decimal('569.00'),
            recorded_by=self.admin,
        )
        EggPOSSalaryPlan.objects.create(
            employee=self.zayyan, effective_month=self.nov,
            monthly_salary=Decimal('30000.00'),
            commission_credit_amount=Decimal('300.00'),
            commission_credit_reason='Nov 1-14 paid commission share approved',
            commission_overlap_ids=[p.pk], created_by=self.admin,
        )
        with patch('api.services.egg_pos_payroll.timezone.localdate', return_value=date(2026, 12, 2)):
            payroll = post_salary(self.zayyan.id, self.nov, self.admin)
        self.assertEqual(payroll.salary_amount, Decimal('30000.00'))
        self.assertEqual(payroll.commission_credit_amount, Decimal('300.00'))
        self.assertEqual(payroll.balance_due, Decimal('29700.00'))
        entry = JournalEntry.objects.get(source_key=f'egg_pos:salary_period:{payroll.pk}')
        lines = {(item.account.code, item.debit, item.credit) for item in entry.lines.select_related('account')}
        self.assertIn(('6390', Decimal('30000.00'), Decimal('0.00')), lines)
        self.assertIn(('2050', Decimal('300.00'), Decimal('0.00')), lines)
        self.assertIn(('6200', Decimal('0.00'), Decimal('300.00')), lines)
        self.assertEqual(EggPOSCommissionPeriod.objects.count(), 1)
        self.assertEqual(EggPOSCommissionPayment.objects.count(), 1)

    def test_zayyan_salary_plan_november_via_screen_preserves_commission(self):
        EggPOSUserAccess.objects.create(user=self.zayyan, can_sell=True)
        period = self._period(self.zayyan, date(2026, 10, 28), date(2026, 11, 14), '569.00')
        EggPOSCommissionPayment.objects.create(
            commission=period, payment_date=date(2026, 11, 14),
            amount=Decimal('569.00'), recorded_by=self.admin,
        )
        self.client.force_login(self.admin)
        with patch('api.views.egg_pos_payroll.timezone.localdate', return_value=date(2026, 11, 14)):
            result = self.client.post(reverse('egg_pos_payroll'), {
                'action': 'plan', 'employee': self.zayyan.pk,
                'effective_month': '2026-11-01', 'monthly_salary': '30000',
                'commission_credit_amount': '300',
                'commission_credit_reason': 'Commission allocated to Nov 1-14 after invoice review',
                'admin_password': 'Secret!nov2026',
            })
        self.assertEqual(result.status_code, 302)
        plan = EggPOSSalaryPlan.objects.get(employee=self.zayyan)
        self.assertEqual(plan.effective_month, self.nov)
        self.assertEqual(plan.commission_credit_amount, Decimal('300.00'))
        self.assertEqual(plan.commission_overlap_ids, [period.id])
        self.assertEqual(EggPOSCommissionPayment.objects.count(), 1)

    def test_owner_cannot_create_fixed_salary_through_ui(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('egg_pos_payroll'), {
            'action': 'plan', 'employee': self.admin.pk, 'effective_month': '2026-11-01',
            'monthly_salary': '10000', 'admin_password': 'Secret!nov2026',
            'commission_credit_amount': '0',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(EggPOSSalaryPlan.objects.filter(employee=self.admin).exists())

    def test_owner_drawing_shortcut_preselects_withdrawal(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse('egg_pos_owner_capital') + '?draw=1')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'value="withdrawal" selected')
