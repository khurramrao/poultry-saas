"""UI-focused tests. Run: python manage.py test api.prebooking_ui_redesign_tests"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from api.models.egg_pos import EggPOSProduct
from api.models.egg_pos_prebooking import (
    EggPOSManualPrebooking as Booking,
    EggPOSManualPrebookingLine as Line,
)


class PrebookingUIRedesignTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            "prebook-redesign-admin", "prebook-ui@example.com", "ui-pass-123"
        )
        self.client.force_login(self.admin)
        self.url = reverse("egg_pos_manual_prebookings")
        self.egg = EggPOSProduct.objects.create(name="White Egg", sku="UI-BOOK-001")
        self.today = timezone.localdate()

    def book(self, *, date, status="awaiting_stock", name="Danish Shah Alam"):
        b = Booking.objects.create(
            created_by=self.admin, customer_name=name, customer_phone="03001234567",
            channel="phone", status=status, expected_date=date,
        )
        Line.objects.create(booking=b, product=self.egg, quantity=3, unit="tray")
        return b

    def test_summary_and_due_filters_exclude_closed_bookings(self):
        self.book(date=self.today)
        self.book(date=self.today - timedelta(days=1), name="Overdue Customer")
        self.book(date=self.today, status="completed", name="Completed Customer")
        self.book(date=self.today + timedelta(days=1), status="contact_customer", name="Future Customer")

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["counts"]["due_today"], 1)
        self.assertEqual(response.context["counts"]["overdue"], 1)
        self.assertEqual(response.context["counts"]["active"], 3)
        self.assertContains(response, "Ready to Contact")
        self.assertContains(response, "Compact")
        self.assertContains(response, "Cards")
        self.assertContains(response, "History &amp; Audit Trail")

        due = self.client.get(self.url, {"status": "due_today"})
        self.assertEqual(len(due.context["bookings"]), 1)
        self.assertTrue(due.context["bookings"][0].is_due_today)

        overdue = self.client.get(self.url, {"status": "overdue"})
        self.assertEqual(len(overdue.context["bookings"]), 1)
        self.assertTrue(overdue.context["bookings"][0].is_overdue)

    def test_search_by_display_booking_number_and_no_inventory_mutation(self):
        booking = self.book(date=self.today, name="Regular Buyer")
        self.book(date=self.today, name="Another Buyer")
        response = self.client.get(self.url, {"status": "all", "q": booking.booking_number})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([b.pk for b in response.context["bookings"]], [booking.pk])
        self.assertContains(response, booking.booking_number)

        # The existing POST status action is unchanged by the UI update.
        response = self.client.post(self.url, {
            "action": "stock_arrived", "booking_id": booking.pk,
        })
        self.assertRedirects(response, self.url)
        booking.refresh_from_db()
        self.assertEqual(booking.status, "contact_customer")
        self.assertTrue(booking.events.filter(action="stock_arrived").exists())
