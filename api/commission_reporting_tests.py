"""Run: python manage.py test api.commission_reporting_tests api.commission_advance_tests"""
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from api.models.egg_pos import (
    EggPOSCommissionPeriod, EggPOSCommissionPayment, EggPOSUserAccess,
)
from api.services.egg_pos_commission_reporting import unposted_date_ranges


def preview(commission, *, net=None):
    value = Decimal(str(commission))
    net = Decimal(str(net)) if net is not None else (value * Decimal("4") if value > 0 else Decimal("-553"))
    return {
        'sales_revenue': Decimal('38100.00'),
        'cogs': Decimal('33183.00'),
        'gross_profit': Decimal('4917.00'),
        'selling_expenses': Decimal('1000.00'),
        'net_after_expenses': net,
        'commissionable_profit': max(net, Decimal('0.00')),
        'commission_percent': Decimal('25.00'),
        'commission_amount': value,
    }


class UnpostedDateRangesTests(TestCase):
    def test_complement_with_boundary_and_multiple_posted_periods(self):
        periods = [
            SimpleNamespace(period_start=date(2026, 9, 25), period_end=date(2026, 10, 3)),
            SimpleNamespace(period_start=date(2026, 10, 10), period_end=date(2026, 10, 12)),
            SimpleNamespace(period_start=date(2026, 10, 12), period_end=date(2026, 10, 16)),
        ]
        self.assertEqual(unposted_date_ranges(date(2026, 10, 1), date(2026, 10, 20), periods), [
            (date(2026, 10, 4), date(2026, 10, 9)),
            (date(2026, 10, 17), date(2026, 10, 20)),
        ])

    def test_fully_posted_does_not_create_new_commission(self):
        p = SimpleNamespace(period_start=date(2026, 10, 1), period_end=date(2026, 10, 20))
        self.assertEqual(unposted_date_ranges(date(2026, 10, 4), date(2026, 10, 15), [p]), [])


class CommissionPeriodReportingTests(TestCase):
    def setUp(self):
        U = get_user_model()
        self.admin = U.objects.create_superuser('commissionreportadmin', 'commission@example.invalid', 'secure-test-password')
        self.staff = U.objects.create_user('commissionreportstaff', password='staff-test-password')
        EggPOSUserAccess.objects.create(
            user=self.staff, can_sell=True, is_active=True, commission_percent=Decimal('25.00'),
        )
        self.period = EggPOSCommissionPeriod.objects.create(
            salesperson=self.staff,
            period_start=date(2026, 10, 1), period_end=date(2026, 10, 20),
            commission_amount=Decimal('1092.00'), commission_percent=Decimal('25.00'),
            posted_by=self.admin,
        )
        EggPOSCommissionPayment.objects.create(
            commission=self.period, amount=Decimal('1000.00'),
            payment_date=date(2026, 10, 20), recorded_by=self.admin,
        )
        self.client.force_login(self.admin)
        self.url = reverse('egg_pos_commissions')

    def test_monthly_view_does_not_rebill_previously_posted_period(self):
        called = []
        def stub(staff, start, end):
            called.append((start, end))
            if (start, end) == (date(2026, 10, 1), date(2026, 10, 22)):
                return preview('979.25')  # old misleading full-range calculation
            if (start, end) == (date(2026, 10, 21), date(2026, 10, 22)):
                return preview('0')
            raise AssertionError(f'unexpected preview period: {start} to {end}')
        with patch('api.views.egg_pos.commission_preview', side_effect=stub):
            resp = self.client.get(self.url, {
                'salesperson': self.staff.id, 'start': '2026-10-01', 'end': '2026-10-22',
            })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(called, [
            (date(2026, 10, 1), date(2026, 10, 22)),
            (date(2026, 10, 21), date(2026, 10, 22)),
        ])
        report = resp.context['commission_period_report']
        self.assertEqual(report['posted_earned'], Decimal('1092.00'))
        self.assertEqual(report['posted_paid'], Decimal('1000.00'))
        self.assertEqual(report['posted_due'], Decimal('92.00'))
        self.assertEqual(report['unposted_estimate'], Decimal('0.00'))
        self.assertEqual(len(report['unposted_rows']), 1)
        self.assertEqual(report['unposted_rows'][0]['start'], date(2026, 10, 21))
        self.assertNotContains(resp, 'Post Commission to Ledger')
        self.assertContains(resp, 'Already Posted')
        self.assertContains(resp, 'Rs 1,092')
        self.assertContains(resp, 'Rs 1,000')
        self.assertContains(resp, 'Rs 92')
        self.assertEqual(EggPOSCommissionPeriod.objects.count(), 1)
        self.assertEqual(EggPOSCommissionPayment.objects.count(), 1)

    def test_new_earnings_only_use_unposted_dates(self):
        with patch('api.views.egg_pos.commission_preview', side_effect=lambda _u, start, end: (
            preview('979.25') if start == date(2026,10,1) else preview('60.00')
        )):
            resp = self.client.get(self.url, {
                'salesperson': self.staff.id, 'start':'2026-10-01','end':'2026-10-22',
            })
        self.assertEqual(resp.context['commission_period_report']['unposted_estimate'], Decimal('60.00'))
        self.assertContains(resp, 'Post Only 21 Oct–22 Oct 2026')
        self.assertNotContains(resp, 'name="period_start" value="2026-10-01"')

    def test_paid_with_advance_allocation_shows_no_unpaid_balance(self):
        from api.models.egg_pos import EggPOSCommissionAdvancePayment, EggPOSCommissionAdvanceAllocation
        self.period.payments.all()  # exercise both payment types
        advance = EggPOSCommissionAdvancePayment.objects.create(
            salesperson=self.staff, payment_date=date(2026,10,21), amount=Decimal('500.00'),
            request_token='abdf4f18-3b6a-4ffc-a9d4-1c0d68a00f01', recorded_by=self.admin,
        )
        EggPOSCommissionAdvanceAllocation.objects.create(
            commission=self.period, payment=advance, amount=Decimal('92.00'),
            applied_date=date(2026,10,21),
        )
        self.assertEqual(self.period.amount_paid, Decimal('1092.00'))
        self.assertEqual(self.period.balance_due, Decimal('0.00'))
        with patch('api.views.egg_pos.commission_preview', return_value=preview('0')):
            resp=self.client.get(self.url, {'salesperson':self.staff.pk,'start':'2026-10-01','end':'2026-10-20'})
        self.assertEqual(resp.context['commission_period_report']['posted_paid'], Decimal('1092.00'))
        self.assertEqual(resp.context['commission_period_report']['posted_due'], Decimal('0.00'))
        self.assertEqual(resp.context['commission_account']['advance_balance'], Decimal('408.00'))


class ConsecutiveCommissionTests(CommissionPeriodReportingTests):
    """Financial regression: no skipping losses or rewriting a paid commission."""

    def _post(self, start, end):
        return self.client.post(self.url, {
            'action': 'post', 'salesperson': self.staff.pk,
            'period_start': start.isoformat(), 'period_end': end.isoformat(),
        }, follow=False)

    def test_skipping_loss_day_is_blocked_server_side_and_in_ui(self):
        from api.services.egg_pos_commission_reporting import commission_with_loss
        with patch('api.views.egg_pos.commission_preview', return_value=preview('58.25', net='233.00')):
            resp = self.client.get(self.url, {
                'salesperson':self.staff.pk,'start':'2026-10-22','end':'2026-10-23'
            })
            self.assertEqual(resp.status_code, 200)
            row = resp.context['commission_period_report']['unposted_rows'][0]
            self.assertFalse(row['eligible'])
            self.assertContains(resp, 'Posting blocked for selected dates')
            self.assertNotContains(resp, 'Post Only 22 Oct')
            self.assertEqual(resp.context['commission_period_report']['unposted_estimate'], Decimal('0.00'))
            response = self._post(date(2026, 10, 22), date(2026, 10, 23))
        from django.contrib.messages import get_messages
        self.assertIn('Commission must start on 21 Oct 2026',
                      ' '.join(m.message for m in get_messages(response.wsgi_request)))
        self.assertEqual(EggPOSCommissionPeriod.objects.count(), 1)
        self.assertEqual(self.period.balance_due, Decimal('92.00'))

    def test_negative_period_can_be_finalized_with_zero_commission_and_loss_carried(self):
        with patch('api.views.egg_pos.commission_preview', return_value=preview('0', net='-553.00')):
            overview = self.client.get(self.url, {
                'salesperson': self.staff.id, 'start':'2026-10-01','end':'2026-10-23',
            })
            row = overview.context['commission_period_report']['unposted_rows'][0]
            self.assertEqual(row['start'], date(2026,10,21))
            self.assertEqual(row['result']['loss_out'], Decimal('553.00'))
            self.assertContains(overview, 'Close 21 Oct–23 Oct 2026')
            self._post(date(2026, 10, 21), date(2026, 10, 23))
        closed = EggPOSCommissionPeriod.objects.get(
            salesperson=self.staff, period_start=date(2026,10,21))
        self.assertEqual(closed.commission_amount, Decimal('0.00'))
        self.assertEqual(closed.commissionable_profit, Decimal('-553.00'))
        self.assertEqual(closed.loss_carry_out, Decimal('553.00'))
        self.assertEqual(closed.loss_carry_in, Decimal('0.00'))
        self.period.refresh_from_db()
        self.assertEqual(self.period.commission_amount, Decimal('1092.00'))
        self.assertEqual(self.period.amount_paid, Decimal('1000.00'))
        self.assertEqual(self.period.balance_due, Decimal('92.00'))
        self.assertEqual(EggPOSCommissionPayment.objects.count(), 1)

    def test_later_positive_profit_first_offsets_previous_loss(self):
        with patch('api.views.egg_pos.timezone.localdate', return_value=date(2026,10,31)), \
             patch('api.views.egg_pos.commission_preview', side_effect=[
                 preview('0', net='-553.00'), preview('250.00', net='1000.00'),
             ]):
            self._post(date(2026, 10, 21), date(2026, 10, 23))
            self._post(date(2026, 10, 24), date(2026, 10, 25))
        latest = EggPOSCommissionPeriod.objects.get(
            salesperson=self.staff, period_start=date(2026,10,24))
        self.assertEqual(latest.loss_carry_in, Decimal('553.00'))
        self.assertEqual(latest.loss_carry_out, Decimal('0.00'))
        self.assertEqual(latest.commission_amount, Decimal('111.75'))
        self.period.refresh_from_db()
        self.assertEqual(self.period.balance_due, Decimal('92.00'))
        self.assertEqual(EggPOSCommissionPeriod.objects.count(), 3)

    def test_partial_loss_recovery_across_more_than_one_period(self):
        from api.services.egg_pos_commission_reporting import commission_with_loss
        with patch('api.views.egg_pos.timezone.localdate', return_value=date(2026,10,31)), \
             patch('api.views.egg_pos.commission_preview', side_effect=[
                 preview('0', net='-553.00'), preview('50.00', net='200.00'),
                 preview('100.00', net='400.00'),
             ]):
            self._post(date(2026,10,21), date(2026,10,23))
            self._post(date(2026,10,24), date(2026,10,25))
            self._post(date(2026,10,26), date(2026,10,27))
        closed = list(EggPOSCommissionPeriod.objects.filter(salesperson=self.staff).order_by('period_start'))
        self.assertEqual([p.loss_carry_out for p in closed], [Decimal('0'), Decimal('553'), Decimal('353'), Decimal('0')])
        self.assertEqual([p.commission_amount for p in closed[1:]],
                         [Decimal('0'), Decimal('0'), Decimal('11.75')])

    def test_earlier_paid_period_cannot_be_recalculated(self):
        with patch('api.views.egg_pos.commission_preview', return_value=preview('20',net='80')):
            self.client.post(self.url, {'action':'recalculate', 'commission_id':self.period.pk}, follow=True)
        self.period.refresh_from_db()
        self.assertEqual(self.period.commission_amount, Decimal('1092.00'))
        self.assertEqual(self.period.balance_due, Decimal('92.00'))

    def test_loss_math_is_independent_of_commission_cash_payments(self):
        from api.services.egg_pos_commission_reporting import commission_with_loss
        res = commission_with_loss(preview('250', net='1000'), Decimal('553'))
        self.assertEqual(res['commissionable_after_loss'], Decimal('447.00'))
        self.assertEqual(res['commission_amount'], Decimal('111.75'))
        self.assertEqual(res['loss_out'], Decimal('0.00'))
        res = commission_with_loss(preview('0', net='-553'), Decimal('125'))
        self.assertEqual(res['loss_out'], Decimal('678.00'))
