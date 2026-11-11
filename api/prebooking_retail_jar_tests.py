"""Run with: python manage.py test api.prebooking_retail_jar_tests"""
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from api.models.egg_pos import EggPOSProduct, EggPOSSale
from api.models.egg_pos_prebooking import (
    EggPOSManualPrebooking as Booking, EggPOSManualPrebookingLine as Line,
)
from api.services.egg_pos_prebooking_whatsapp import build_prebooking_whatsapp
from api.services.egg_pos_prebooking_sale import prefill_for_sale


class RetailJarPrebookingTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            username="jar-prebooking-admin", email="jar@example.com", password="test-password"
        )
        self.client.force_login(self.admin)
        self.url = reverse("egg_pos_manual_prebookings")
        self.eggs = EggPOSProduct.objects.create(name="Organic Eggs", sku="T-PBE", category="egg")
        self.ghee = EggPOSProduct.objects.create(name="Buffalo Desi Ghee 1000gm", sku="T-PBG", category="ghee")
        self.honey = EggPOSProduct.objects.create(name="Honey 500gm", sku="T-PBH", category="honey")

    def payload(self, product, unit, qty="1", **changes):
        data = dict(action="create", request_token=str(uuid4()),
                    customer_name="Muhammad Raza", customer_phone="03001234567",
                    channel="whatsapp", product_1=str(product.pk),
                    quantity_1=qty, unit_1=unit, quoted_unit_price_1="3299",
                    expected_date="2026-11-16")
        data.update(changes)
        return data

    def test_ghee_jar_is_one_jar_not_thirty_eggs(self):
        res = self.client.post(self.url, self.payload(self.ghee, "jar"))
        self.assertEqual(res.status_code, 302)
        booking = Booking.objects.get()
        self.assertEqual(booking.total_eggs, 0)
        self.assertEqual(booking.total_jars, 1)
        self.assertEqual(booking.total_items_display, "1 jar")
        self.assertEqual(booking.quoted_items_total, Decimal("3299.00"))
        msg = build_prebooking_whatsapp(booking)["message"]
        self.assertIn("1 jar", msg)
        self.assertNotIn("30 eggs", msg)
        self.assertEqual(EggPOSSale.objects.count(), 0)

    def test_honey_jars_and_mixed_eggs(self):
        data = self.payload(self.eggs, "tray", "2", product_2=str(self.honey.pk),
                            quantity_2="2", unit_2="jar", quoted_unit_price_2="1450")
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        book = Booking.objects.get()
        self.assertEqual(book.total_eggs, 60)
        self.assertEqual(book.total_jars, 2)
        self.assertEqual(book.total_items_display, "60 eggs · 2 jars")
        self.assertEqual(book.quoted_items_total, Decimal("9498.00"))
        self.assertIn("60 eggs · 2 jars", build_prebooking_whatsapp(book)["message"])

    def test_server_rejects_jar_egg_unit_mismatch(self):
        for product, unit in ((self.ghee, "tray"), (self.honey, "dozen"), (self.eggs, "jar")):
            with self.subTest(product=product.sku, unit=unit):
                res = self.client.post(self.url, self.payload(product, unit))
                self.assertEqual(res.status_code, 200)
                self.assertEqual(Booking.objects.count(), 0)

    def test_edit_form_contains_product_category_and_sealed_jar(self):
        page = self.client.get(self.url)
        self.assertContains(page, 'data-category="ghee"')
        self.assertContains(page, 'data-category="honey"')
        self.assertContains(page, 'value="jar"')
        self.assertContains(page, 'Sealed Jars')

    def test_pos_prefill_uses_one_jar_not_tray(self):
        self.client.post(self.url, self.payload(self.ghee, "jar", "2"))
        book = Booking.objects.get()
        book.status = "confirmed"
        book.save(update_fields=["status"])
        payload = prefill_for_sale(self.admin, book.pk, [])
        self.assertEqual(len(payload["items"]), 1)
        item = payload["items"][0]
        self.assertEqual(item["unit"], "jar")
        self.assertEqual(item["unit_count"], 2)
        self.assertEqual(item["eggs"], 2)  # Legacy cart key stores physical count, not eggs.
        self.assertEqual(item["stock_unit"], "jar")

    def test_duplicate_jar_lines_prefill_as_summed_jars(self):
        self.client.post(self.url, self.payload(self.ghee, "jar", "2",
                                                product_2=str(self.ghee.pk),
                                                quantity_2="3", unit_2="jar"))
        book = Booking.objects.get()
        book.status = "confirmed"
        book.save(update_fields=["status"])
        item = prefill_for_sale(self.admin, book.pk, [])["items"][0]
        self.assertEqual((item["unit"], item["unit_count"], item["eggs"]), ("jar", 5, 5))

    def test_old_invalid_ghee_tray_cannot_convert_silently(self):
        book = Booking.objects.create(created_by=self.admin, customer_name="Old Ghee Booking",
                                      customer_phone="03001234567", channel="phone", status="confirmed")
        Line.objects.create(booking=book, product=self.ghee, quantity=1, unit="tray")
        with self.assertRaisesMessage(ValidationError, "Sealed Jars"):
            prefill_for_sale(self.admin, book.pk, [])

    def test_existing_egg_bookings_work_as_before(self):
        self.assertEqual(self.client.post(self.url, self.payload(self.eggs, "tray", "2")).status_code, 302)
        book = Booking.objects.get()
        self.assertEqual(book.total_items_display, "60 eggs")
        book.status = "confirmed"
        book.save(update_fields=["status"])
        item = prefill_for_sale(self.admin, book.pk, [])["items"][0]
        self.assertEqual((item["unit"], item["unit_count"], item["eggs"]), ("tray", 2, 60))
