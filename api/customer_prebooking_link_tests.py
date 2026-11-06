"""Run: python manage.py test api.customer_prebooking_link_tests"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from api.models.egg_pos import EggPOSCustomer, EggPOSProduct, EggPOSSale
from api.models.egg_pos_prebooking import EggPOSManualPrebooking
from api.services.egg_pos_prebooking_sale import prefill_for_sale


class ExistingCustomerPrebookingTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            username="existing-customer-admin", email="admin@example.com", password="test-pass-123"
        )
        self.staff = get_user_model().objects.create_user(
            username="existing-customer-staff", password="test-pass-456"
        )
        self.customer = EggPOSCustomer.objects.create(name="Danish Shah Alam", phone="")
        self.product = EggPOSProduct.objects.create(name="Organic Eggs", sku="EGG-EXIST-1")
        self.detail = reverse("egg_pos_customer_detail", kwargs={"customer_id": self.customer.id})
        self.bookings = reverse("egg_pos_manual_prebookings")
        self.client.force_login(self.admin)

    def test_customer_page_contains_edit_and_prebooking_link(self):
        response = self.client.get(self.detail)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Edit Customer Details / Phone")
        self.assertContains(response, f"?customer={self.customer.id}#entry")

    def test_can_save_phone_without_changing_prior_invoices(self):
        sale = EggPOSSale.objects.create(sale_number="RNE-CUSTTEST-1", customer=self.customer)
        response = self.client.post(self.detail, {
            "name": "Danish Shah Alam", "phone": "03001234567", "company_name": "",
            "address": "", "notes": "",
        })
        self.assertRedirects(response, self.detail)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.phone, "03001234567")
        sale.refresh_from_db()
        self.assertEqual(sale.customer_id, self.customer.id)

    def test_prefills_and_links_manual_prebooking(self):
        self.customer.phone = "03001234567"
        self.customer.save()
        response = self.client.get(self.bookings, {"customer": self.customer.id})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Danish Shah Alam")
        self.assertContains(response, 'name="customer_id"')
        token = response.context["request_token"]
        payload = {
            "action": "create", "request_token": token, "customer_id": self.customer.id,
            "customer_name": self.customer.name, "customer_phone": self.customer.phone,
            "channel": "whatsapp", "product_1": self.product.id,
            "quantity_1": "2", "unit_1": "tray",
        }
        response = self.client.post(self.bookings, payload)
        self.assertRedirects(response, self.bookings)
        booking = EggPOSManualPrebooking.objects.get()
        self.assertEqual(booking.customer_id, self.customer.id)
        self.assertEqual(booking.customer_phone, "03001234567")
        self.assertEqual(booking.get_channel_display(), "WhatsApp")
        booking.status = "confirmed"
        booking.save()
        draft = prefill_for_sale(self.admin, booking.id, [self.customer])
        self.assertEqual(draft["customer_id"], self.customer.id)

    def test_no_prebooking_without_saved_phone(self):
        response = self.client.get(self.bookings, {"customer": self.customer.id})
        data = {"action":"create", "request_token": response.context["request_token"],
                "customer_id":self.customer.id, "customer_name":self.customer.name,
                "customer_phone":"03001234567", "channel":"whatsapp",
                "product_1":self.product.id,"quantity_1":"1","unit_1":"tray"}
        response = self.client.post(self.bookings, data)
        self.assertEqual(EggPOSManualPrebooking.objects.count(), 0)

    def test_non_admin_cannot_edit_customer_or_make_booking(self):
        self.staff.is_staff = False
        self.staff.save()
        self.client.force_login(self.staff)
        before = self.customer.phone
        response = self.client.post(self.detail, {"name": self.customer.name, "phone":"03001234567"})
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.phone, before)
        response = self.client.get(self.bookings)
        self.assertEqual(response.status_code, 403)
