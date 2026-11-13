"""Regression checks: manual jar requests must appear without reserving stock."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from api.models.egg_pos import EggPOSProduct, EggPOSInventoryLot, EggPOSSupplier
from api.models.egg_pos_prebooking import (
    EggPOSManualPrebooking,
    EggPOSManualPrebookingLine,
)
from api.services.egg_pos_sku_report import make_report


class SKUManualJarBookingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = get_user_model().objects.create_superuser(
            'manualjaradmin', 'manualjars@example.test', 'test-password'
        )
        cls.supplier = EggPOSSupplier.objects.create(name='Retail SKU Test Supplier')
        cls.ghee = EggPOSProduct.objects.create(
            name='Buffalo Desi Ghee 1000gm', sku='GHEE-BUF-1000-TEST',
            category='ghee', weight_grams=1000,
        )
        cls.honey = EggPOSProduct.objects.create(
            name='Honey 500gm', sku='HONEY-500-TEST',
            category='honey', weight_grams=500,
        )
        cls.egg = EggPOSProduct.objects.create(
            name='Organic Egg', sku='EGG-ORG-TEST', category='egg',
        )
        cls.outside_egg = EggPOSProduct.objects.create(
            name='Purchased Organic Egg', sku='EGG-ORG-OUTSIDE-TEST',
            category='egg', selling_product=cls.egg,
        )
        EggPOSInventoryLot.objects.create(
            product=cls.ghee, supplier=cls.supplier, source_type='purchase',
            lot_code='JAR-TEST-001', received_date=timezone.localdate(),
            quantity_received=2, quantity_remaining=2,
            unit_cost=Decimal('2300.00'),
        )

    def add_booking(self, product, quantity, unit, *, status='awaiting_stock'):
        booking = EggPOSManualPrebooking.objects.create(
            customer_name='Retail test customer', customer_phone='03001234567',
            channel='whatsapp', created_by=self.staff, status=status,
        )
        EggPOSManualPrebookingLine.objects.create(
            booking=booking, product=product, quantity=quantity, unit=unit,
        )
        return booking

    def report_group(self, product):
        report = make_report(timezone.localdate(), timezone.localdate())
        return next(g for g in report['groups'] if g['primary'].pk == product.pk)

    def test_two_ghee_jar_prebookings_count_as_two_manual_requests(self):
        self.add_booking(self.ghee, 1, 'jar')
        self.add_booking(self.ghee, 1, 'jar')
        group = self.report_group(self.ghee)
        self.assertEqual(group['manual_requests'], 2)
        self.assertEqual(group['on_hand'], 2)
        self.assertEqual(group['online_booked'], 0)
        # Manual/social bookings are requests, not physical reservations.
        self.assertEqual(group['available'], 2)

    def test_honey_jar_and_egg_tray_counts_are_not_mixed(self):
        booking = self.add_booking(self.honey, 3, 'jar')
        EggPOSManualPrebookingLine.objects.create(
            booking=booking, product=self.outside_egg,
            quantity=2, unit='tray',
        )
        self.assertEqual(self.report_group(self.honey)['manual_requests'], 3)
        self.assertEqual(self.report_group(self.egg)['manual_requests'], 60)

    def test_inactive_bookings_are_excluded_but_confirmed_included(self):
        self.add_booking(self.ghee, 1, 'jar', status='cancelled')
        self.add_booking(self.ghee, 1, 'jar', status='completed')
        self.add_booking(self.ghee, 2, 'jar', status='confirmed')
        self.assertEqual(self.report_group(self.ghee)['manual_requests'], 2)

    def test_invalid_legacy_egg_units_do_not_inflate_jar_requests(self):
        self.add_booking(self.ghee, 1, 'tray')
        self.add_booking(self.egg, 1, 'jar')
        self.assertEqual(self.report_group(self.ghee)['manual_requests'], 0)
        self.assertEqual(self.report_group(self.egg)['manual_requests'], 0)

    def test_date_filter_does_not_hide_live_manual_requests(self):
        self.add_booking(self.ghee, 2, 'jar')
        previous = timezone.localdate() - timedelta(days=30)
        report = make_report(previous, previous, category='ghee')
        self.assertEqual(len(report['groups']), 1)
        self.assertEqual(report['groups'][0]['manual_requests'], 2)
        self.assertEqual(report['groups'][0]['sold'], 0)
