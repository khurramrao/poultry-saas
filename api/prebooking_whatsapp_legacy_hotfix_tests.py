"""Regression tests for deployed pre-0091 models without quotation fields."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from django.test import SimpleTestCase

from api.services.egg_pos_prebooking_whatsapp import build_prebooking_whatsapp


class WhatsAppPrebookingLegacyCompatibilityTests(SimpleTestCase):
    def _booking(self, *, new_fields=False, partial=False):
        product = SimpleNamespace(name="Organic Egg")
        first = SimpleNamespace(product=product, quantity=1, unit="tray", eggs_count=30)
        lines = [first]
        if partial:
            lines.append(SimpleNamespace(product=SimpleNamespace(name="White Egg"),
                                         quantity=1, unit="dozen", eggs_count=12))
        if new_fields:
            first.quoted_unit_price = Decimal("1050.00")
            first.quoted_rate_unit = "tray"
            first.quoted_line_total = Decimal("1050.00")
            if partial:
                lines[1].quoted_unit_price = None
        booking = SimpleNamespace(status="awaiting_stock", customer_name="Muhammad Altaf",
                                  customer_phone="03334225588", booking_number="RNPRE-000017",
                                  expected_date=date(2026, 11, 13),
                                  delivery_area="DHA Phase 1, Lahore",
                                  lines=SimpleNamespace(all=lambda: lines))
        if new_fields:
            booking.delivery_window_display = "3:00 PM – 5:00 PM"
            booking.quoted_items_total = None if partial else Decimal("1050.00")
        return booking

    def test_legacy_booking_without_rate_or_window_fields(self):
        result = build_prebooking_whatsapp(self._booking())
        self.assertIn("*Your Pre-Booking Confirmation*", result["message"])
        self.assertIn("Thank you for choosing", result["message"])
        self.assertIn("RNPRE-000017", result["message"])
        self.assertNotIn("Quoted Rates", result["message"])
        self.assertNotIn("Preferred Delivery Time", result["message"])
        self.assertEqual(result["label"], "Send Booking Receipt")
        self.assertEqual(parse_qs(urlparse(result["url"]).query)["text"][0], result["message"])

    def test_legacy_booking_with_multiple_lines_still_has_no_fake_prices(self):
        msg = build_prebooking_whatsapp(self._booking(partial=True))["message"]
        self.assertIn("*Total:* 42 eggs", msg)
        self.assertNotIn("Rs 0", msg)

    def test_new_rate_time_fields_preserved(self):
        msg = build_prebooking_whatsapp(self._booking(new_fields=True))["message"]
        self.assertIn("Rs 1,050.00", msg)
        self.assertIn("3:00 PM – 5:00 PM", msg)
        self.assertIn("*Quoted Items Total:* Rs 1,050.00", msg)

    def test_partially_priced_new_booking_has_no_misleading_total(self):
        msg = build_prebooking_whatsapp(self._booking(new_fields=True, partial=True))["message"]
        self.assertIn("Rate not set", msg)
        self.assertIn("Not finalized (some rates missing)", msg)

    def test_cancelled_booking_creates_no_message(self):
        booking = self._booking()
        booking.status = "cancelled"
        self.assertIsNone(build_prebooking_whatsapp(booking))
