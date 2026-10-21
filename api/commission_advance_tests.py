"""Run: python manage.py test api.commission_advance_tests"""
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.utils import timezone

from api.models.egg_pos import (
    EggPOSCommissionPeriod, EggPOSCommissionPayment,
    EggPOSCommissionAdvancePayment, EggPOSCommissionAdvanceAllocation, EggPOSUserAccess,
)
from api.services.accounting import normal_account_balance
from api.services.egg_pos_accounting import sync_commission_period, sync_commission_payment
from api.services.egg_pos_commission_advances import (
    record_commission_cash_payment, apply_commission_advances, commission_account_summary,
)


def d(x):
    return Decimal(x)


class CommissionAdvanceTests(TestCase):
    def setUp(self):
        U = get_user_model()
        self.admin = U.objects.create_superuser('rnadmintest', 'admin@example.invalid', 'AdminPassword123')
        self.staff = U.objects.create_user('rncommstaff', password='staff123')
        self.today = timezone.localdate()
        EggPOSUserAccess.objects.create(user=self.staff, can_sell=True, is_active=True, commission_percent=d('25.00'))

    def period(self, amount, *, day_offset, duration=6):
        end = self.today + timedelta(days=day_offset)
        start = end-timedelta(days=duration)
        period = EggPOSCommissionPeriod.objects.create(
            salesperson=self.staff, period_start=start, period_end=end,
            commission_amount=d(amount), commission_percent=d('25.00'), posted_by=self.admin,
        )
        sync_commission_period(period)
        return period

    def pay(self, amount, token=None, method='cash'):
        return record_commission_cash_payment(
            salesperson_id=self.staff.id, amount=d(amount), payment_date=self.today,
            payment_method=method, reference='TEST', notes='commission payment',
            recorded_by=self.admin, request_token=token or str(uuid4()),
        )

    def test_advance_after_previous_payment_then_future_earned(self):
        # Example: posted Rs 1092; Rs 1000 previously paid via old UI.
        prior = self.period('1092.00', day_offset=-1)
        legacy = EggPOSCommissionPayment.objects.create(
            commission=prior, payment_date=self.today, amount=d('1000'),
            payment_method='cash', recorded_by=self.admin,
        )
        sync_commission_payment(legacy)
        payment, applied, duplicate = self.pay('500.00')
        self.assertFalse(duplicate)
        self.assertEqual(applied, d('92.00'))
        self.assertEqual(payment.advance_remaining, d('408.00'))
        self.assertEqual(prior.balance_due, d('0.00'))
        self.assertEqual(normal_account_balance('2030', party_type='user', party_id=self.staff.id), d('0.00'))
        self.assertEqual(normal_account_balance('1150', party_type='user', party_id=self.staff.id), d('408.00'))
        # A newly posted commission uses the remaining advance without extra cash.
        future = self.period('700.00', day_offset=0)
        self.assertEqual(apply_commission_advances(self.staff.id), d('408.00'))
        self.assertEqual(future.balance_due, d('292.00'))
        self.assertEqual(payment.advance_remaining, d('0.00'))
        self.assertEqual(normal_account_balance('2030', party_type='user', party_id=self.staff.id), d('292.00'))
        self.assertEqual(normal_account_balance('1150', party_type='user', party_id=self.staff.id), d('0.00'))
        self.assertEqual(commission_account_summary(self.staff)['total_paid'], d('1500.00'))

    def test_no_posted_commission_is_all_advance(self):
        payment, applied, duplicate = self.pay('800.00', method='bank_transfer')
        self.assertEqual(applied, d('0.00'))
        self.assertEqual(payment.advance_remaining, d('800.00'))
        self.assertEqual(normal_account_balance('1150', party_type='user', party_id=self.staff.id), d('800.00'))
        self.assertEqual(normal_account_balance('1010'), d('-800.00'))
        account = commission_account_summary(self.staff)
        self.assertEqual(account['advance_balance'], d('800.00'))
        self.assertEqual(account['posted_due'], d('0.00'))

    def test_oldest_period_first_and_payment_cash_posted_once(self):
        oldest = self.period('100.00', day_offset=-8)
        newer = self.period('200.00', day_offset=-1)
        payment, applied, _ = self.pay('175.00')
        self.assertEqual(applied, d('175.00'))
        self.assertEqual(oldest.amount_paid, d('100.00'))
        self.assertEqual(newer.amount_paid, d('75.00'))
        self.assertEqual(newer.balance_due, d('125.00'))
        self.assertEqual(EggPOSCommissionAdvanceAllocation.objects.count(), 2)
        self.assertEqual(normal_account_balance('1000'), d('-175.00'))
        self.assertEqual(normal_account_balance('1150', party_type='user', party_id=self.staff.id), d('0.00'))
        self.assertEqual(apply_commission_advances(self.staff.id), d('0.00'))
        self.assertEqual(EggPOSCommissionAdvanceAllocation.objects.count(), 2)

    def test_duplicate_token_does_not_send_cash_twice(self):
        token=str(uuid4())
        first, applied, dup1 = self.pay('300.00',token=token)
        second, applied_again, dup2 = self.pay('300.00',token=token)
        self.assertEqual(first.id,second.id)
        self.assertFalse(dup1)
        self.assertTrue(dup2)
        self.assertEqual(applied_again, d('0.00'))
        self.assertEqual(EggPOSCommissionAdvancePayment.objects.count(),1)
        self.assertEqual(normal_account_balance('1000'), d('-300.00'))

    def test_no_overpayment_and_no_extra_commission_expense(self):
        self.period('200.00', day_offset=-1)
        self.pay('500.00')
        self.assertEqual(normal_account_balance('1150', party_type='user', party_id=self.staff.id), d('300.00'))
        self.assertEqual(normal_account_balance('6200'),d('200.00'))
        self.assertEqual(normal_account_balance('2030',party_type='user',party_id=self.staff.id),d('0.00'))

    def test_no_clearing_another_person_period(self):
        U=get_user_model()
        other = U.objects.create_user('rnanotherstaff', password='password123')
        period = EggPOSCommissionPeriod.objects.create(
            salesperson=other, period_start=self.today-timedelta(days=2), period_end=self.today,
            commission_amount=d('100'), commission_percent=d('25'), posted_by=self.admin,
        )
        sync_commission_period(period)
        self.pay('500.00')
        self.assertEqual(period.balance_due,d('100.00'))

    def test_admin_password_required_in_view(self):
        from django.urls import reverse
        self.client.force_login(self.admin)
        url=reverse('egg_pos_commissions')
        data={
            'action':'record_payment', 'salesperson':str(self.staff.pk),
            'amount':'500.00', 'payment_method':'cash',
            'payment_date':str(self.today), 'request_token':str(uuid4()),
        }
        response=self.client.post(url,{**data,'admin_password':'wrong'})
        self.assertEqual(response.status_code,200)
        self.assertFalse(EggPOSCommissionAdvancePayment.objects.exists())
        response=self.client.post(url,{**data,'admin_password':'AdminPassword123'})
        self.assertEqual(response.status_code,302)
        self.assertEqual(EggPOSCommissionAdvancePayment.objects.count(),1)
        self.assertEqual(normal_account_balance('1150',party_type='user',party_id=self.staff.pk),d('500.00'))

    def test_salesperson_cannot_record_advances(self):
        from django.urls import reverse
        self.client.force_login(self.staff)
        resp=self.client.post(reverse('egg_pos_commissions'),{
            'action':'record_payment','salesperson':str(self.staff.pk),
            'amount':'100','payment_date':str(self.today),'payment_method':'cash',
            'admin_password':'staff123','request_token':str(uuid4()),
        })
        self.assertEqual(EggPOSCommissionAdvancePayment.objects.count(),0)
