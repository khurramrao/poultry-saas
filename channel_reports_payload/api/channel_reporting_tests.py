"""Run with: python manage.py test api.channel_reporting_tests"""
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from api.models.egg_pos import EggPOSSale
from api.models.egg_pos_marketing import EggPOSMarketingSale
from api.models.online_shop import OnlineOrder
from api.services.egg_pos_channel_reports import (
    build_source_report, build_repeat_report, order_source_report,
)

class ChannelReportUnitTests(TestCase):
    def test_tiktok_invoice_counts_even_without_identified_customer(self):
        rows, totals = build_source_report(
            [(1, 'RNE-00013', Decimal('400.00'))],
            set(), {1: 'tiktok'}, {})
        tiktok = next(x for x in rows if x['key'] == 'tiktok')
        self.assertEqual((tiktok['invoices'], tiktok['revenue']), (1, Decimal('400')))
        self.assertEqual(totals['revenue'], Decimal('400'))

    def test_web_invoice_counts_even_without_marketing_profile(self):
        rows, totals = build_source_report(
            [(11, 'RNE-00011', Decimal('1825')),
             (12, 'RNE-00012', Decimal('1525'))],
            {11, 12}, {}, {})
        website = next(x for x in rows if x['key'] == 'website')
        self.assertEqual((website['invoices'], website['revenue']), (2, Decimal('3350')))

    def test_completed_booking_fallback_does_not_guess_ambiguous_sources(self):
        rows, _ = build_source_report(
            [(1, 'INV-1', 250), (2, 'INV-2', 150)], set(), {},
            {'INV-1': ['facebook'], 'INV-2': ['facebook', 'tiktok']})
        self.assertEqual(next(x for x in rows if x['key']=='facebook')['invoices'], 1)
        self.assertEqual(next(x for x in rows if x['key']=='ambiguous')['invoices'], 1)

    def test_no_duplicate_invoice_when_marketing_and_booking_both_linked(self):
        rows, total = build_source_report(
            [(1, 'INV-1', 400)], set(), {1: 'tiktok'}, {'INV-1': ['tiktok']})
        self.assertEqual(total['invoices'], 1)

    def test_only_repeats_contribute_to_repeat_revenue(self):
        rows = build_repeat_report(
            [(8, 'tiktok'), (9, 'facebook')],
            [(8, Decimal('1000')), (9, Decimal('500')),
             (8, Decimal('3000')), (8, Decimal('200'))])
        tiktok = next(x for x in rows if x['label'] == 'TikTok')
        self.assertEqual(tiktok['repeat_customers'], 1)
        self.assertEqual(tiktok['repeat_orders'], 2)
        self.assertEqual(tiktok['repeat_revenue'], Decimal('3200'))
        self.assertEqual(next(x for x in rows if x['label']=='Facebook')['repeat_orders'], 0)

    def test_order_source_report_excludes_reversed_invoices(self):
        user = get_user_model().objects.create_user(username='channel_report_test_user')
        a = EggPOSSale.objects.create(sale_number='RNE-TEST-CH-A', net_total=Decimal('400'),
                                      created_by=user, customer_name='Example')
        b = EggPOSSale.objects.create(sale_number='RNE-TEST-CH-B', net_total=Decimal('300'),
                                      created_by=user, customer_name='Example', is_reversed=True)
        EggPOSMarketingSale.objects.create(sale=a, order_source='tiktok', recorded_by=user)
        EggPOSMarketingSale.objects.create(sale=b, order_source='facebook', recorded_by=user)
        rows, total = order_source_report()
        self.assertEqual(total['invoices'], 1)
        self.assertEqual(next(x for x in rows if x['key']=='facebook')['invoices'], 0)
        self.assertEqual(next(x for x in rows if x['key']=='tiktok')['revenue'], Decimal('400'))
