"""Run: python manage.py test api.prebooking_upgrade_tests"""
from decimal import Decimal
from types import SimpleNamespace
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TestCase
from django.urls import reverse
from api.models.egg_pos import EggPOSProduct, EggPOSSale
from api.models.egg_pos_prebooking import EggPOSManualPrebooking as Booking, EggPOSManualPrebookingLine as Line
from api.services.egg_pos_prebooking_sale import prefill_for_sale, lock_confirmed_for_sale, complete_with_sale


class PrebookingUpgradeTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser('booktester', 'booktester@example.com', 'x-12345')
        self.product = EggPOSProduct.objects.create(name='White Eggs', sku='SOC-0001')
        self.url = reverse('egg_pos_manual_prebookings')
        self.client.force_login(self.admin)

    def make_book(self, source, status='confirmed', ref=''):
        booking = Booking.objects.create(
            created_by=self.admin, customer_name='Mina', customer_phone='03001234567',
            channel=source, status=status, invoice_reference=ref,
        )
        Line.objects.create(booking=booking, product=self.product, quantity=2, unit='tray')
        return booking

    def test_prefill_no_sale_and_phone_normalization(self):
        booking = self.make_book('tiktok')
        customer = SimpleNamespace(id=47, phone='+92 300 1234567')
        draft = prefill_for_sale(self.admin, booking.pk, [customer])
        self.assertEqual(draft['customer_id'], 47)
        self.assertEqual(draft['channel'], 'TikTok')
        self.assertEqual(draft['items'][0]['unit_count'], 2)
        self.assertEqual(draft['items'][0]['eggs'], 60)
        self.assertEqual(EggPOSSale.objects.count(), 0)

    def test_atomic_conversion_link_and_duplicate_block(self):
        booking = self.make_book('facebook')
        sale = EggPOSSale.objects.create(sale_number='RNE-00125', net_total=Decimal('1800.00'))
        with transaction.atomic():
            locked = lock_confirmed_for_sale(self.admin, booking.pk)
            complete_with_sale(locked, sale, self.admin)
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'completed')
        self.assertEqual(booking.invoice_reference, 'RNE-00125')
        self.assertTrue(booking.events.filter(action='completed').exists())
        with transaction.atomic():
            with self.assertRaises(ValidationError):
                lock_confirmed_for_sale(self.admin, booking.pk)

    def test_reports_verified_only_excludes_fake_and_reversed(self):
        self.make_book('tiktok', 'completed', 'RNE-00201')
        self.make_book('tiktok', 'completed', 'NOT_A_REAL_INVOICE')
        self.make_book('instagram', 'completed', 'RNE-00202')
        self.make_book('facebook', 'cancelled')
        self.make_book('whatsapp', 'confirmed')
        EggPOSSale.objects.create(sale_number='RNE-00201', net_total=Decimal('2750.00'), is_reversed=False)
        EggPOSSale.objects.create(sale_number='RNE-00202', net_total=Decimal('990.00'), is_reversed=True)
        response = self.client.get(self.url, {'report_month': 'all'})
        self.assertEqual(response.status_code, 200)
        rows = {r['key']: r for r in response.context['channel_rows']}
        self.assertEqual(rows['tiktok']['leads'], 2)
        self.assertEqual(rows['tiktok']['invoiced'], 1)
        self.assertEqual(rows['tiktok']['invoiced_value'], Decimal('2750.00'))
        self.assertEqual(rows['instagram']['invoiced'], 0)
        self.assertEqual(rows['facebook']['cancelled'], 1)
        self.assertEqual(response.context['channel_total']['invoiced'], 1)

    def test_form_is_collapsed_until_opened(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'pb-entry')
        self.assertContains(response, '+ Add another egg product')
        self.assertContains(response, 'Which Platform Brings Sales?')
