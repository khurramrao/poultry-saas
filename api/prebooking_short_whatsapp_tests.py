"""Concise WhatsApp and old/new booking-model compatibility regressions."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from django.test import SimpleTestCase
from api.services.egg_pos_prebooking_whatsapp import build_prebooking_whatsapp


class ShortWhatsAppTests(SimpleTestCase):
    def booking(self, *, priced=True, window=True, partial=False):
        lines = [SimpleNamespace(product=SimpleNamespace(name="Organic Egg"),
                                 quantity=1, unit="tray", eggs_count=30)]
        if priced:
            lines[0].quoted_unit_price = Decimal("1050.00")
            lines[0].quoted_rate_unit = "tray"
        if partial:
            lines.append(SimpleNamespace(product=SimpleNamespace(name="White Egg"),
                                         quantity=1, unit="dozen", eggs_count=12,
                                         quoted_unit_price=None))
        book = SimpleNamespace(status="awaiting_stock", customer_name="MUHAMMAD ALTAF",
                               customer_phone="03334225588", booking_number="RNPRE-000017",
                               expected_date=date(2026, 11, 13),
                               delivery_area="B-37 PHASE 1 DEFENCE",
                               lines=SimpleNamespace(all=lambda: lines))
        if window:
            book.delivery_window_display = "3:00 PM – 5:00 PM"
        return book

    def test_compact_customer_receipt_has_rate_delivery_time_and_ref(self):
        result = build_prebooking_whatsapp(self.booking())
        message = result["message"]
        self.assertLess(len(message), 710)
        self.assertIn("Thank you for choosing", message)
        self.assertIn("Your Pre-Booking Confirmation", message)
        self.assertIn("RNPRE-000017", message)
        self.assertIn("Rs 1,050.00 per tray", message)
        self.assertIn("*Quoted Items Total:* Rs 1,050.00", message)
        self.assertIn("3:00 PM – 5:00 PM", message)
        self.assertIn("your order is not yet confirmed", message)
        self.assertEqual(parse_qs(urlparse(result["url"]).query)["text"][0], message)

    def test_old_model_never_crashes_or_guesses_prices(self):
        msg = build_prebooking_whatsapp(self.booking(priced=False, window=False))["message"]
        self.assertNotIn("Rs 1,050", msg)
        self.assertNotIn("Preferred Delivery Time", msg)
        self.assertIn("Organic Egg: 1 tray (30 eggs)", msg)

    def test_partial_prices_are_not_misleading(self):
        msg = build_prebooking_whatsapp(self.booking(partial=True))["message"]
        self.assertIn("Not finalized (some rates missing)", msg)
        self.assertNotIn("*Quoted Items Total:* Rs 1,050.00", msg)

    def test_status_labels_change_without_side_effects(self):
        booking = self.booking()
        for code, action in (("contact_customer", "Request Confirmation"),
                             ("confirmed", "Send Confirmation"),
                             ("completed", "Send Follow-Up")):
            booking.status = code
            self.assertEqual(build_prebooking_whatsapp(booking)["label"], action)
        booking.status = "cancelled"
        self.assertIsNone(build_prebooking_whatsapp(booking))
