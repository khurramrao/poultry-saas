"""Run: python manage.py test api.admin_finance_update_tests"""
import uuid
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from api.models.accounting import JournalEntry
from api.models.egg_pos_admin_loan import EggPOSAdminLoanTransaction
from api.models.egg_pos_admin_expense import EggPOSAdminExpense
from api.services.accounting import line, post_entry, raw_account_balance
from api.services.egg_pos_admin_loans import loan_outstanding, record_admin_loan, rebuild_admin_loans
from api.services.egg_pos_admin_expenses import rebuild_admin_expenses


class RayNoorAdminFinanceTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(username='finance_admin_test', email='finance@example.test', password='AdminFinance!123')
        self.other = get_user_model().objects.create_user(username='finance_staff_test', password='StaffFinance!123')
        self.client.force_login(self.admin)
        post_entry(source_key='test:adminfinance:cash', entry_date=timezone.localdate(), reference='TEST-CASH',
                   lines=[line('1000',debit=Decimal('5000')),line('3000',credit=Decimal('5000'))])
        post_entry(source_key='test:adminfinance:bank', entry_date=timezone.localdate(), reference='TEST-BANK',
                   lines=[line('1010',debit=Decimal('5000')),line('3000',credit=Decimal('5000'))])

    def test_admin_loan_and_repayment_do_not_affect_profit(self):
        a,_ = record_admin_loan(administrator_id=self.admin.pk,kind='advance',amount='3000',payment_source='cash',recorded_by=self.admin,request_token=uuid.uuid4())
        self.assertEqual(loan_outstanding(self.admin.pk),Decimal('3000.00'))
        self.assertEqual(raw_account_balance('1000'),Decimal('2000.00'))
        b,_ = record_admin_loan(administrator_id=self.admin.pk,kind='repayment',amount='1200',payment_source='bank',recorded_by=self.admin,request_token=uuid.uuid4())
        self.assertEqual(loan_outstanding(self.admin.pk),Decimal('1800.00'))
        self.assertEqual(raw_account_balance('1010'),Decimal('6200.00'))
        self.assertEqual(raw_account_balance('1320'),Decimal('1800.00'))
        for item in (a,b):
            e = JournalEntry.objects.get(source_key=f'egg_pos:admin_loan:{item.pk}')
            self.assertEqual({l.account.code for l in e.lines.all()}, {'1320', '1000' if item.pk==a.pk else '1010'})
        rebuild_admin_loans()
        self.assertEqual(JournalEntry.objects.filter(source_type='egg_pos_admin_loan').count(),2)

    def test_loan_rejects_overpayment_and_insufficient_funds(self):
        with self.assertRaisesMessage(ValueError,'Insufficient'):
            record_admin_loan(administrator_id=self.admin.pk,kind='advance',amount='9000',payment_source='cash',recorded_by=self.admin,request_token=uuid.uuid4())
        a,_=record_admin_loan(administrator_id=self.admin.pk,kind='advance',amount='200',payment_source='cash',recorded_by=self.admin,request_token=uuid.uuid4())
        with self.assertRaisesMessage(ValueError,'Repayment exceeds'):
            record_admin_loan(administrator_id=self.admin.pk,kind='repayment',amount='201',payment_source='cash',recorded_by=self.admin,request_token=uuid.uuid4())
        self.assertEqual(EggPOSAdminLoanTransaction.objects.count(),1)

    def test_duplicate_loan_submission_only_one_journal(self):
        token=uuid.uuid4()
        a,repeat=record_admin_loan(administrator_id=self.admin.pk,kind='advance',amount='100',payment_source='bank',recorded_by=self.admin,request_token=token)
        b,repeat2=record_admin_loan(administrator_id=self.admin.pk,kind='advance',amount='100',payment_source='bank',recorded_by=self.admin,request_token=token)
        self.assertEqual(a.pk,b.pk)
        self.assertFalse(repeat)
        self.assertTrue(repeat2)
        self.assertEqual(EggPOSAdminLoanTransaction.objects.count(),1)

    def test_admin_expense_correction_reverses_old_journal_and_posts_new(self):
        orig=EggPOSAdminExpense.objects.create(expense_date=timezone.localdate(),category='rent',amount=Decimal('500'),payment_source='cash', recorded_by=self.admin)
        from api.services.egg_pos_admin_expenses import sync_admin_expense
        sync_admin_expense(orig)
        url=reverse('egg_pos_correct_admin_expense',args=[orig.pk])
        payload={'expense_date':timezone.localdate().isoformat(),'category':'utilities', 'amount':'700',
                 'payment_source':'bank','paid_to':'Utility vendor','reference':'BILL-42','notes':'Correct expense',
                 'reason':'Original entry was wrongly classified and paid via bank', 'admin_password':'AdminFinance!123'}
        response=self.client.post(url,payload)
        self.assertEqual(response.status_code,302)
        orig.refresh_from_db()
        self.assertTrue(orig.is_voided)
        self.assertEqual(EggPOSAdminExpense.objects.filter(is_voided=False).count(),1)
        self.assertEqual(raw_account_balance('1000'),Decimal('5000.00'))
        self.assertEqual(raw_account_balance('1010'),Decimal('4300.00'))
        self.assertEqual(raw_account_balance('6300'),Decimal('0.00'))
        self.assertEqual(raw_account_balance('6310'),Decimal('700.00'))
        self.client.post(url,payload)
        self.assertEqual(EggPOSAdminExpense.objects.count(),2)
        rebuild_admin_expenses()
        self.assertEqual(raw_account_balance('1000'),Decimal('5000.00'))

    def test_unprivileged_user_cannot_correct_or_lend(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('egg_pos_admin_loans')).status_code,403)
        self.assertEqual(self.client.post(reverse('egg_pos_admin_loans'),{'kind':'advance'}).status_code,403)
