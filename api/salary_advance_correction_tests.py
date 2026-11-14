"""Run python manage.py test api.salary_advance_correction_tests"""
from datetime import date
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from api.models.egg_pos import EggPOSUserAccess
from api.models.egg_pos_payroll import (
    EggPOSSalaryAdvance, EggPOSSalaryAdvanceCorrection,
    EggPOSSalaryAdvanceAllocation, EggPOSSalaryPlan, EggPOSPayrollPeriod,
)
from api.models.accounting import JournalEntry
from api.services.accounting import line, post_entry, normal_account_balance
from api.services.egg_pos_payroll import record_salary_advance, correct_unapplied_salary_advance


class SalaryAdvanceCorrectionTests(TestCase):
    def setUp(self):
        U = get_user_model()
        self.admin = U.objects.create_superuser(username='advance_correction_admin',password='FixAdvance2026!')
        self.employee = U.objects.create_user(username='advance_correction_staff',password='staffPassword1!')
        EggPOSUserAccess.objects.create(user=self.employee, can_sell=True)
        EggPOSSalaryPlan.objects.create(employee=self.employee,
            effective_month=date(2026,11,1),monthly_salary=Decimal('4000.00'),created_by=self.admin)
        post_entry(source_key='test:advance_cash',entry_date=date(2026,11,1),created_by=self.admin,
            lines=[line('1000',debit=Decimal('20000.00')),line('3000',credit=Decimal('20000.00'))])
        with patch('api.services.egg_pos_payroll.timezone.localdate',return_value=date(2026,11,14)):
            self.advance,_=record_salary_advance(self.employee.id,Decimal('1036.00'),
                date(2026,11,14),'cash',self.admin,uuid4(),reference='Test payment')

    def test_reduces_overstated_advance_without_creating_new_payment(self):
        correction=correct_unapplied_salary_advance(self.advance.id,Decimal('936.00'),
            'Mistyped 1036 instead of actual cash paid 936',self.admin)
        self.advance.refresh_from_db()
        self.assertEqual(self.advance.amount,Decimal('936.00'))
        self.assertEqual(self.advance.available,Decimal('936.00'))
        self.assertEqual(correction.old_amount,Decimal('1036.00'))
        self.assertEqual(correction.new_amount,Decimal('936.00'))
        self.assertEqual(EggPOSSalaryAdvance.objects.count(),1)
        self.assertEqual(normal_account_balance('1000'),Decimal('19064.00'))
        self.assertEqual(normal_account_balance('1310'),Decimal('936.00'))
        self.assertEqual(JournalEntry.objects.filter(source_key=f'egg_pos:salary_advance:{self.advance.id}').count(),1)

    def test_audit_preserves_multiple_corrections(self):
        correct_unapplied_salary_advance(self.advance.pk,Decimal('1000.00'),'First correction from paid cash slip',self.admin)
        correct_unapplied_salary_advance(self.advance.pk,Decimal('936.00'),'Second correction per verified receipt',self.admin)
        self.assertEqual(EggPOSSalaryAdvanceCorrection.objects.filter(advance=self.advance).count(),2)
        self.assertEqual(normal_account_balance('1310'),Decimal('936.00'))

    def test_rejects_increase_and_already_applied_amount(self):
        with self.assertRaisesMessage(ValueError,'only reduces'):
            correct_unapplied_salary_advance(self.advance.id,Decimal('1100.00'),'Increase the cash paid amount',self.admin)
        p=EggPOSPayrollPeriod.objects.create(employee=self.employee,month=date(2026,11,1),salary_amount=Decimal('4000'),posted_by=self.admin)
        EggPOSSalaryAdvanceAllocation.objects.create(advance=self.advance,payroll=p,amount=Decimal('100.00'))
        with self.assertRaisesMessage(ValueError,'already applied'):
            correct_unapplied_salary_advance(self.advance.id,Decimal('936.00'),'Correcting wrong original amount',self.admin)
        self.advance.refresh_from_db()
        self.assertEqual(self.advance.amount,Decimal('1036.00'))

    def test_rejects_non_superuser(self):
        with self.assertRaisesMessage(ValueError,'superuser'):
            correct_unapplied_salary_advance(self.advance.id,Decimal('936.00'),'I recorded the wrong amount',self.employee)
        self.assertEqual(EggPOSSalaryAdvanceCorrection.objects.count(),0)

    def test_admin_screen_edit_with_password_and_audit(self):
        self.client.force_login(self.admin)
        response=self.client.post(reverse('egg_pos_payroll'),{'action':'correct_advance',
            'advance_id':self.advance.id,'corrected_amount':'936.00',
            'reason':'The cash receipt shows only Rs 936 paid','admin_password':'FixAdvance2026!'})
        self.assertEqual(response.status_code,302)
        self.advance.refresh_from_db()
        self.assertEqual(self.advance.amount,Decimal('936.00'))
        self.assertEqual(EggPOSSalaryAdvanceCorrection.objects.count(),1)
        page=self.client.get(reverse('egg_pos_payroll'))
        self.assertContains(page,'Salary Advance Correction Audit')
        self.assertContains(page,'The cash receipt shows only Rs 936 paid')

    def test_invalid_admin_password_does_not_correct(self):
        self.client.force_login(self.admin)
        response=self.client.post(reverse('egg_pos_payroll'),{'action':'correct_advance',
            'advance_id':self.advance.id,'corrected_amount':'936.00',
            'reason':'Wrong original entry amount', 'admin_password':'wrong'})
        self.assertEqual(response.status_code,200)
        self.advance.refresh_from_db()
        self.assertEqual(self.advance.amount,Decimal('1036.00'))
        self.assertFalse(EggPOSSalaryAdvanceCorrection.objects.exists())
