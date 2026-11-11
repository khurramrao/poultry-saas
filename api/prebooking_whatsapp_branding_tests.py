"""Tests for branded, status-correct RayNoor WhatsApp pre-booking drafts."""
from datetime import date, time
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.test import TestCase

from api.models.egg_pos import EggPOSProduct, EggPOSSale
from api.models.egg_pos_prebooking import (
    EggPOSManualPrebooking as Booking,
    EggPOSManualPrebookingLine as Line,
)
from api.services.egg_pos_prebooking_whatsapp import build_prebooking_whatsapp


class PrebookingWhatsAppBrandingTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            "brand-wa-admin", "brand-wa@example.com", "password"
        )
        product = EggPOSProduct.objects.create(name="Organic Egg", sku="BR-WA-ORG")
        self.booking = Booking.objects.create(
            created_by=self.admin,
            customer_name="Muhammad Altaf",
            customer_phone="03334225588",
            channel="tiktok",
            expected_date=date(2026, 11, 13),
            delivery_time_from=time(15, 0),
            delivery_time_to=time(17, 0),
            delivery_area="DHA Phase 1, Lahore",
            status="awaiting_stock",
        )
        Line.objects.create(
            booking=self.booking, product=product,
            quantity=1, unit="tray", quoted_unit_price=Decimal("1050.00"),
        )

    def test_branded_receipt_contains_rate_time_and_cautious_status(self):
        before = EggPOSSale.objects.count()
        result = build_prebooking_whatsapp(self.booking)
        message = result["message"]
        self.assertIn("*RayNoor Organic Farms*", message)
        self.assertIn("*Your Pre-Booking Confirmation*", message)
        self.assertIn("Thank you for choosing *RayNoor Organic Farms*!", message)
        self.assertIn("Muhammad Altaf", message)
        self.assertIn(self.booking.booking_number, message)
        self.assertIn("Organic Egg", message)
        self.assertIn("Rs 1,050.00", message)
        self.assertIn("3:00 PM – 5:00 PM", message)
        self.assertIn("13 Nov 2026", message)
        self.assertIn("Awaiting stock", message)
        self.assertIn("your order is not yet confirmed", message)
        self.assertIn("We look forward to serving you!", message)
        self.assertIn("please reply to this WhatsApp message", message)
        self.assertEqual(parse_qs(urlparse(result["url"]).query)["text"][0], message)
        self.assertEqual(result["label"], "Send Booking Receipt")
        self.assertEqual(EggPOSSale.objects.count(), before)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, "awaiting_stock")
        self.assertFalse(self.booking.events.exists())

    def test_state_specific_headings_and_next_steps(self):
        cases = [
            ("contact_customer", "Pre-Booking Stock Update", "Request Confirmation", "Please reply to confirm"),
            ("confirmed", "Your Pre-Booking Is Confirmed", "Send Confirmation", "Customer reconfirmed"),
            ("completed", "Thank You for Your Pre-Booking", "Send Follow-Up", "Processed"),
        ]
        for state, heading, label, detail in cases:
            with self.subTest(state=state):
                self.booking.status = state
                result = build_prebooking_whatsapp(self.booking)
                self.assertIn(heading, result["message"])
                self.assertIn(detail, result["message"])
                self.assertIn("Thank you for choosing", result["message"])
                self.assertEqual(result["label"], label)
        self.booking.status = "cancelled"
        self.assertIsNone(build_prebooking_whatsapp(self.booking))
