"""Run: python manage.py test api.salary_payroll_tests"""
import uuid
from datetime import date
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from api.models.egg_pos_payroll import (
    EggPOSSalaryPlan, EggPOSPayrollPeriod, EggPOSSalaryAdvance, EggPOSSalaryAdvanceAllocation,
    EggPOSSalaryPayment,
)
from api.services.egg_pos_payroll import (
    post_salary, record_salary_advance, record_salary_payment, salary_cutoff,
)


class SalaryPayrollTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(username='payroll_admin', password='example_admin_pass_456')
        self.staff = get_user_model().objects.create_user(username='payroll_employee', password='example_staff_pass_456')
        self.plan = EggPOSSalaryPlan.objects.create(
            employee=self.staff, effective_month=date(2026, 1, 1),
            monthly_salary=Decimal('5000.00'), created_by=self.admin,
        )

    def test_salary_month_posts_once_and_pays_after_advances(self):
        advance, repeated = record_salary_advance(
            self.staff.id, Decimal('1000.00'), date(2026, 1, 10), 'cash',
            self.admin, uuid.uuid4(), 'TEST-A',
        )
        self.assertFalse(repeated)
        payroll = post_salary(self.staff.pk, date(2026, 1, 1), self.admin)
        self.assertEqual(payroll.advances_applied, Decimal('1000.00'))
        self.assertEqual(payroll.balance_due, Decimal('4000.00'))
        self.assertEqual(advance.available, Decimal('0.00'))
        payment, repeated = record_salary_payment(
            payroll.pk, Decimal('4000.00'), date(2026, 2, 2), 'bank',
            self.admin, uuid.uuid4(), 'TEST-P',
        )
        self.assertFalse(repeated)
        self.assertEqual(payroll.balance_due, Decimal('0.00'))
        with self.assertRaisesMessage(ValueError, 'already been posted'):
            post_salary(self.staff.pk, date(2026, 1, 1), self.admin)

    def test_idempotent_advance_and_payment(self):
        token = uuid.uuid4()
        a1, repeat1 = record_salary_advance(self.staff.pk, Decimal('500.00'), date(2026, 1, 15), 'cash', self.admin, token)
        a2, repeat2 = record_salary_advance(self.staff.pk, Decimal('500.00'), date(2026, 1, 15), 'cash', self.admin, token)
        self.assertFalse(repeat1)
        self.assertTrue(repeat2)
        self.assertEqual(a1.pk, a2.pk)
        self.assertEqual(EggPOSSalaryAdvance.objects.count(), 1)
        payroll = post_salary(self.staff.pk, date(2026, 1, 1), self.admin)
        ptoken = uuid.uuid4()
        p1, _ = record_salary_payment(payroll.pk, Decimal('100.00'), date(2026, 2, 2), 'bank', self.admin, ptoken)
        p2, repeat = record_salary_payment(payroll.pk, Decimal('100.00'), date(2026, 2, 2), 'bank', self.admin, ptoken)
        self.assertEqual(p1.pk, p2.pk)
        self.assertTrue(repeat)
        self.assertEqual(EggPOSSalaryPayment.objects.count(), 1)

    def test_multiple_advances_applied_oldest_first(self):
        a1, _ = record_salary_advance(self.staff.pk, Decimal('3000.00'), date(2026, 1, 3), 'cash', self.admin, uuid.uuid4())
        a2, _ = record_salary_advance(self.staff.pk, Decimal('3000.00'), date(2026, 1, 4), 'bank', self.admin, uuid.uuid4())
        p1 = post_salary(self.staff.pk, date(2026, 1, 1), self.admin)
        self.assertEqual(p1.advances_applied, Decimal('5000.00'))
        self.assertEqual(a1.available, Decimal('0.00'))
        self.assertEqual(a2.available, Decimal('1000.00'))
        p2 = post_salary(self.staff.pk, date(2026, 2, 1), self.admin)
        self.assertEqual(p2.advances_applied, Decimal('1000.00'))
        self.assertEqual(p2.balance_due, Decimal('4000.00'))

    def test_overpayment_rejected_and_commission_cutoff_stored(self):
        p = post_salary(self.staff.pk, date(2026, 1, 1), self.admin)
        with self.assertRaisesMessage(ValueError, 'exceeds'):
            record_salary_payment(p.pk, Decimal('5000.01'), date(2026, 2, 2), 'cash', self.admin, uuid.uuid4())
        self.assertEqual(EggPOSSalaryPayment.objects.count(), 0)
        self.assertEqual(salary_cutoff(self.staff.pk), date(2026, 1, 1))

    def test_only_admin_can_view_payroll(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse('egg_pos_payroll')).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('egg_pos_payroll')).status_code, 200)
