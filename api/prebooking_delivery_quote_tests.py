"""Run: python manage.py test api.prebooking_delivery_quote_tests"""
from datetime import date, time
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from api.models.egg_pos import EggPOSProduct, EggPOSSale
from api.models.egg_pos_prebooking import (
    EggPOSManualPrebooking as Booking,
    EggPOSManualPrebookingLine as Line,
)
from api.services.egg_pos_prebooking_whatsapp import build_prebooking_whatsapp


class PrebookingDeliveryQuoteTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            "quote-test-admin", "quote-test@example.com", "test-password"
        )
        self.client.force_login(self.admin)
        self.url = reverse("egg_pos_manual_prebookings")
        self.organic = EggPOSProduct.objects.create(name="Organic Egg", sku="QT-ORG-1")
        self.white = EggPOSProduct.objects.create(name="White Egg", sku="QT-WHT-1")

    def payload(self, **kwargs):
        import uuid
        data = {
            "action": "create", "request_token": str(uuid.uuid4()),
            "customer_name": "Muhammad Altaf", "customer_phone": "03334225588",
            "channel": "tiktok", "delivery_area": "DHA Phase 1 Lahore",
            "expected_date": "2026-11-13", "delivery_time_from": "15:00", "delivery_time_to": "17:00",
            "product_1": str(self.organic.id), "quantity_1": "1", "unit_1": "tray",
            "quoted_unit_price_1": "1050.00", "notes": "Rider will call before delivery",
        }
        data.update(kwargs)
        return data

    def test_create_saves_rate_and_time_without_creating_sale(self):
        response = self.client.post(self.url, self.payload())
        self.assertEqual(response.status_code, 302)
        book = Booking.objects.get()
        self.assertEqual(book.expected_date, date(2026, 11, 13))
        self.assertEqual(book.delivery_time_from, time(15, 0))
        self.assertEqual(book.delivery_time_to, time(17, 0))
        self.assertEqual(book.delivery_window_display, "3:00 PM – 5:00 PM")
        self.assertEqual(book.quoted_items_total, Decimal("1050.00"))
        line = book.lines.get()
        self.assertEqual(line.quoted_unit_price, Decimal("1050.00"))
        self.assertEqual(line.quoted_line_total, Decimal("1050.00"))
        self.assertEqual(EggPOSSale.objects.count(), 0)
        self.assertEqual(book.status, "awaiting_stock")

        screen = self.client.get(self.url)
        self.assertContains(screen, "3:00 PM")
        self.assertContains(screen, "1,050.00")
        self.assertContains(screen, "Agreed / quoted item rates")

        wa = build_prebooking_whatsapp(book)
        self.assertIn("Rs 1,050.00", wa["message"])
        self.assertIn("per tray", wa["message"])
        self.assertIn("3:00 PM – 5:00 PM", wa["message"])
        self.assertIn("tentative", wa["message"])
        self.assertIn("your order is not yet confirmed", wa["message"])

    def test_multi_line_total_and_partially_priced_bookings(self):
        payload = self.payload(
            product_2=str(self.white.id), quantity_2="2", unit_2="dozen",
            quoted_unit_price_2="440.00",
        )
        self.assertEqual(self.client.post(self.url, payload).status_code, 302)
        book = Booking.objects.get()
        self.assertEqual(book.quoted_items_total, Decimal("1930.00"))
        self.assertIn("Rs 1,930.00", build_prebooking_whatsapp(book)["message"])
        Line.objects.filter(booking=book, product=self.white).update(quoted_unit_price=None)
        self.assertIsNone(Booking.objects.get(pk=book.pk).quoted_items_total)
        self.assertIn("Not finalized", build_prebooking_whatsapp(Booking.objects.get(pk=book.pk))["message"])

    def test_old_bookings_without_time_or_rates_still_work(self):
        book = Booking.objects.create(
            created_by=self.admin, customer_name="Old Customer", customer_phone="03001234567",
            channel="phone", expected_date=date(2026, 11, 15),
        )
        Line.objects.create(booking=book, product=self.organic, quantity=1, unit="tray")
        self.assertIsNone(book.quoted_items_total)
        self.assertEqual(book.delivery_window_display, "")
        wa = build_prebooking_whatsapp(book)
        self.assertNotIn("Quoted Items Total", wa["message"])
        self.assertNotIn("Preferred Delivery Time", wa["message"])
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_edit_updates_rates_and_time_with_audit(self):
        self.assertEqual(self.client.post(self.url, self.payload()).status_code, 302)
        book = Booking.objects.get()
        data = self.payload(action="update", booking_id=str(book.pk),
                            quoted_unit_price_1="1100", delivery_time_from="16:00",
                            delivery_time_to="18:00")
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        book.refresh_from_db()
        self.assertEqual(book.quoted_items_total, Decimal("1100.00"))
        self.assertEqual(book.delivery_window_display, "4:00 PM – 6:00 PM")
        self.assertTrue(book.events.filter(action="edited", details__contains="1050.00").exists())
        self.assertEqual(EggPOSSale.objects.count(), 0)

    def test_reject_missing_reversed_window_and_invalid_rates(self):
        bad_data = [
            {"delivery_time_to": ""},
            {"delivery_time_to": "14:30"},
            {"delivery_time_from": ""},
            {"expected_date": ""},
            {"quoted_unit_price_1": "-10"},
            {"quoted_unit_price_1": "1050.001"},
            {"quoted_unit_price_1": "not-a-number"},
            {"quoted_unit_price_1": "NaN"},
            {"product_1": "", "quantity_1": "", "quoted_unit_price_1": "1050"},
        ]
        for changes in bad_data:
            with self.subTest(changes=changes):
                response = self.client.post(self.url, self.payload(**changes))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(Booking.objects.count(), 0)

    def test_zero_rate_is_not_missing(self):
        response = self.client.post(self.url, self.payload(quoted_unit_price_1="0"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Booking.objects.get().quoted_items_total, Decimal("0.00"))
