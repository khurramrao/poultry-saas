"""Tests for readable individual payroll details in RayNoor income statement."""
from datetime import date
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from api.services.accounting import income_statement, post_entry, line
from api.models.egg_pos_payroll import EggPOSSalaryPlan


class SalaryIncomeStatementTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(username='admin', password='testing-only', first_name='Rao', last_name='Zahi')
        self.iqra = User.objects.create_superuser(username='iqramanzoor', password='testing-only', first_name='Iqra', last_name='Manzoor')
        self.zayyan = User.objects.create_user(username='zayyan', password='testing-only', first_name='Zayyan')
        self.abbas = User.objects.create_user(username='abbas', password='testing-only')
        EggPOSSalaryPlan.objects.create(employee=self.zayyan, effective_month=date(2026, 11, 1), monthly_salary=Decimal('4000'), created_by=self.admin)
        EggPOSSalaryPlan.objects.create(employee=self.abbas, effective_month=date(2026, 11, 1), monthly_salary=Decimal('3500'), created_by=self.admin)
        self.when = date(2026, 11, 14)

    def journal(self, key, lines, source_type='unit_test'):
        return post_entry(source_key=key, entry_date=self.when, source_type=source_type, reference=key, created_by=self.admin, lines=lines)

    def test_variable_admin_salary_and_advance_are_shown_separately(self):
        self.journal('revenue', [line('1000', debit='10000'), line('4000', credit='10000')])
        self.journal('iqra_salary', [line('6390', debit='2000', party_type='user', party_id=self.iqra.id), line('1000', credit='2000')])
        self.journal('commission', [line('6200', debit='569.07', party_type='user', party_id=self.zayyan.id), line('2030', credit='569.07')])
        self.journal('advance', [line('1310', debit='936', party_type='user', party_id=self.zayyan.id), line('1000', credit='936')], source_type='egg_pos_salary_advance')
        self.journal('owner_drawings', [line('3000', debit='500', party_type='user', party_id=self.admin.id), line('1000', credit='500')])
        report = income_statement(date(2026, 11, 1), self.when)
        self.assertEqual(report['salary_expense'], Decimal('2000.00'))
        self.assertEqual(report['commission_expense'], Decimal('569.07'))
        self.assertEqual(report['expenses'], Decimal('2569.07'))
        self.assertEqual(report['net_profit'], Decimal('7430.93'))
        self.assertEqual(report['advances_paid'], Decimal('936.00'))
        byname = {x['name']: x['amount'] for x in report['salary_by_person']}
        self.assertEqual(byname['Iqra Manzoor (iqramanzoor)'], Decimal('2000.00'))
        self.assertEqual(byname['Rao Zahi (admin)'], Decimal('0.00'))
        self.assertEqual(byname['Zayyan (zayyan)'], Decimal('0.00'))
        self.assertEqual(next(x for x in report['advances_paid_by_person'] if x['name'].startswith('Zayyan'))['amount'], Decimal('936.00'))

    def test_allocation_from_advance_does_not_count_as_new_payment(self):
        self.journal('a1', [line('1310', debit='936', party_type='user', party_id=self.zayyan.id), line('1000', credit='936')], source_type='egg_pos_salary_advance')
        self.journal('allocation', [line('2050', debit='936'), line('1310', credit='936', party_type='user', party_id=self.zayyan.id)], source_type='egg_pos_salary_advance_allocation')
        report = income_statement(date(2026, 11, 1), self.when)
        self.assertEqual(report['advances_paid'], Decimal('936.00'))
        self.assertEqual(report['expenses'], Decimal('0.00'))

    def test_legacy_unassigned_salary_is_visible(self):
        self.journal('older_salary', [line('6390', debit='350'), line('1000', credit='350')])
        report = income_statement(date(2026, 11, 1), self.when)
        self.assertIn({'name':'Unassigned staff', 'amount':Decimal('350.00')}, report['salary_by_person'])
        self.assertEqual(report['salary_expense'], Decimal('350.00'))
