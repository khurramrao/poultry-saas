"""Run: python manage.py test api.marketing_attribution_tests"""
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from api.models.egg_pos import EggPOSSale, EggPOSCustomer
from api.models.egg_pos_marketing import EggPOSMarketingCustomer, EggPOSMarketingSale
from api.models.egg_pos_prebooking import EggPOSManualPrebooking
from api.services.egg_pos_marketing import (
    normalize_phone, normalize_name, record_marketing_sale,
    decorate_sales, acquisition_report,
)


class MarketingAttributionTests(TestCase):
    def setUp(self):
        self.admin_user = get_user_model().objects.create_user(
            username='market_test_admin', password='example-test-password'
        )

    def sale(self, name, phone, total='1000', customer=None):
        return EggPOSSale.objects.create(
            sale_number=f'RNE-MARKET-{EggPOSSale.objects.count()+1}',
            customer_name=name, customer_phone=phone, customer=customer,
            net_total=Decimal(total), created_by=self.admin_user,
        )

    def test_phone_normalization(self):
        self.assertEqual(normalize_phone('0321 1234567'), '923211234567')
        self.assertEqual(normalize_phone('+92-321-1234567'), '923211234567')
        self.assertEqual(normalize_phone('0092 321 1234567'), '923211234567')
        self.assertEqual(normalize_phone('123'), '')
        self.assertEqual(normalize_name('  Yasir    FOODS '), 'yasir foods')

    def test_first_tiktok_later_whatsapp_is_repeat_not_new_tiktok_order(self):
        booking = EggPOSManualPrebooking.objects.create(
            customer_name='Ali Khan', customer_phone='03211234567',
            channel='tiktok', created_by=self.admin_user,
        )
        first = self.sale('Ali Khan', '+92 321 1234567')
        record_marketing_sale(first, booking, 'direct', self.admin_user)
        second = self.sale('Ali Khan', '03211234567', '3000')
        record_marketing_sale(second, None, 'whatsapp', self.admin_user)
        a = EggPOSMarketingSale.objects.get(sale=first)
        b = EggPOSMarketingSale.objects.get(sale=second)
        self.assertEqual(a.order_source, 'tiktok')
        self.assertEqual(b.order_source, 'whatsapp')
        self.assertEqual(b.marketing_customer, a.marketing_customer)
        self.assertEqual(b.marketing_customer.acquired_from, 'tiktok')
        decorated = decorate_sales([first, second])
        self.assertFalse(decorated[0].raynoor_repeat_customer)
        self.assertTrue(decorated[1].raynoor_repeat_customer)
        row = next(x for x in acquisition_report() if x['label'] == 'TikTok')
        self.assertEqual(row['customers'], 1)
        self.assertEqual(row['orders'], 2)
        self.assertEqual(row['repeat_orders'], 1)
        self.assertEqual(row['revenue'], Decimal('4000'))

    def test_family_phone_different_names_remain_separate(self):
        a = self.sale('Ali Khan', '03211234567')
        b = self.sale('Sara Khan', '+923211234567')
        record_marketing_sale(a, None, 'facebook', self.admin_user)
        record_marketing_sale(b, None, 'whatsapp', self.admin_user)
        self.assertEqual(EggPOSMarketingCustomer.objects.count(), 2)
        self.assertNotEqual(a.marketing_attribution.marketing_customer_id,
                            b.marketing_attribution.marketing_customer_id)

    def test_unidentified_invoice_keeps_order_source_without_merging(self):
        sale = self.sale('Walk-in', '')
        record_marketing_sale(sale, None, 'instagram', self.admin_user)
        a = EggPOSMarketingSale.objects.get(sale=sale)
        self.assertEqual(a.order_source, 'instagram')
        self.assertIsNone(a.marketing_customer)

    def test_customer_directory_id_keeps_known_customer(self):
        c = EggPOSCustomer.objects.create(name='Ali Khan', phone='03211234567')
        sale_a = self.sale('Ali Khan', c.phone, customer=c)
        record_marketing_sale(sale_a, None, 'tiktok', self.admin_user)
        sale_b = self.sale('Ali Khan Updated', c.phone, customer=c)
        record_marketing_sale(sale_b, None, 'phone', self.admin_user)
        self.assertEqual(sale_a.marketing_attribution.marketing_customer_id,
                         sale_b.marketing_attribution.marketing_customer_id)

    def test_reversed_sale_excluded_from_lifetime_value(self):
        sale_a = self.sale('Ali Khan', '03211234567')
        record_marketing_sale(sale_a, None, 'facebook', self.admin_user)
        sale_a.is_reversed = True
        sale_a.save(update_fields=['is_reversed'])
        row = next(x for x in acquisition_report() if x['label'] == 'Facebook')
        self.assertEqual(row['customers'], 1)
        self.assertEqual(row['orders'], 0)
        self.assertEqual(row['revenue'], Decimal('0'))
