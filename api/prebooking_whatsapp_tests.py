"""Run: python manage.py test api.prebooking_whatsapp_tests"""
from datetime import date
from urllib.parse import urlparse, parse_qs

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from api.models.egg_pos import EggPOSProduct
from api.models.egg_pos_prebooking import (
    EggPOSManualPrebooking as Booking,
    EggPOSManualPrebookingLine as Line,
)
from api.services.egg_pos_prebooking_whatsapp import (
    normalize_whatsapp_number,
    build_prebooking_whatsapp,
)


class PrebookingWhatsAppTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            "whatsapp-test-admin", "wa-test@example.com", "test-password"
        )
        self.client.force_login(self.admin)
        self.url = reverse("egg_pos_manual_prebookings")
        self.organic = EggPOSProduct.objects.create(name="Organic Egg", sku="WA-ORG-45")
        self.white = EggPOSProduct.objects.create(name="White Egg 607", sku="WA-WHT-607")
        self.booking = Booking.objects.create(
            created_by=self.admin,
            customer_name="Maira Malik",
            customer_phone="0300 1234567",
            channel="tiktok",
            expected_date=date(2026, 11, 10),
            delivery_area="Johar Town, Lahore",
            status="awaiting_stock",
        )
        Line.objects.create(booking=self.booking, product=self.organic, quantity=1, unit="dozen")
        Line.objects.create(booking=self.booking, product=self.white, quantity=1, unit="dozen")

    def test_phone_normalization(self):
        self.assertEqual(normalize_whatsapp_number("0300 1234567"), "923001234567")
        self.assertEqual(normalize_whatsapp_number("+92 (300) 1234567"), "923001234567")
        self.assertEqual(normalize_whatsapp_number("00923001234567"), "923001234567")
        self.assertEqual(normalize_whatsapp_number("3001234567"), "923001234567")
        self.assertEqual(normalize_whatsapp_number("invalid"), "")
        self.assertEqual(normalize_whatsapp_number("01234567890"), "")

    def test_awaiting_stock_receipt_is_not_false_confirmation(self):
        message = build_prebooking_whatsapp(self.booking)
        url = urlparse(message["url"])
        self.assertEqual(url.netloc, "wa.me")
        self.assertEqual(url.path, "/923001234567")
        content = parse_qs(url.query)["text"][0]
        self.assertIn(self.booking.booking_number, content)
        self.assertIn("Maira Malik", content)
        self.assertIn("Organic Egg: 1 dozen (12 eggs)", content)
        self.assertIn("White Egg 607: 1 dozen (12 eggs)", content)
        self.assertIn("Total:* 24 eggs", content)
        self.assertIn("10 Nov 2026", content)
        self.assertIn("Johar Town, Lahore", content)
        self.assertIn("Awaiting stock", content)
        self.assertIn("your order is not yet confirmed", content)
        self.assertNotIn("invoice", content.lower())
        self.assertEqual(message["label"], "Send Booking Receipt")

    def test_contact_customer_and_reconfirmed_have_distinct_messages(self):
        self.booking.status = "contact_customer"
        follow_up = build_prebooking_whatsapp(self.booking)
        self.assertEqual(follow_up["label"], "Request Confirmation")
        self.assertIn("Please reply to confirm", follow_up["message"])

        self.booking.status = "confirmed"
        confirmed = build_prebooking_whatsapp(self.booking)
        self.assertEqual(confirmed["label"], "Send Confirmation")
        self.assertIn("Customer reconfirmed", confirmed["message"])

        self.booking.status = "cancelled"
        self.assertIsNone(build_prebooking_whatsapp(self.booking))

    def test_screen_has_action_without_mutating_booking(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Send Booking Receipt")
        self.assertContains(response, "https://wa.me/923001234567?text=")
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, "awaiting_stock")
        self.assertFalse(self.booking.events.exists())
        self.assertFalse(self.booking.invoice_reference)

    def test_invalid_phone_cannot_create_send_link(self):
        self.booking.customer_phone = "unknown"
        self.booking.save(update_fields=["customer_phone"])
        response = self.client.get(self.url)
        self.assertNotContains(response, "Send Booking Receipt")
        self.assertContains(response, "Add or correct the customer")
