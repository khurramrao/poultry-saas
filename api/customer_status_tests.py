"""Focused customer status regression tests.

Run: python manage.py test api.customer_status_tests
"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from api.models.egg_pos import EggPOSCustomer, EggPOSSale
from api.services.egg_pos_marketing import decorate_sales, record_marketing_sale


class CustomerStatusTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="status_test_cashier", password="testpass123")

    def sale(self, name="Customer", customer=None, phone="", source=None):
        sale = EggPOSSale.objects.create(
            sale_number=f"RNE-STATUS-{EggPOSSale.objects.count() + 1}",
            customer=customer, customer_name=name, customer_phone=phone,
            created_by=self.user,
        )
        if source:
            record_marketing_sale(sale, None, source, self.user)
        return sale

    def test_saved_customer_first_and_second_sale_without_marketing_history(self):
        customer = EggPOSCustomer.objects.create(name="Frie line Muslim town")
        first = self.sale(name=customer.name, customer=customer)
        second = self.sale(name=customer.name, customer=customer)
        first, second = decorate_sales([first, second])
        self.assertEqual(first.raynoor_customer_status, "New")
        self.assertEqual(second.raynoor_customer_status, "Returning")

    def test_unlinked_names_are_unverified_instead_of_assumed_new(self):
        a = self.sale(name="Walk-in")
        b = self.sale(name="Walk-in")
        for sale in decorate_sales([a, b]):
            self.assertEqual(sale.raynoor_customer_status, "Unverified")

    def test_verified_contact_can_return_across_sales_sources(self):
        first = self.sale(name="Ali Khan", phone="03211234567", source="tiktok")
        second = self.sale(name="Ali Khan", phone="+923211234567", source="whatsapp")
        first, second = decorate_sales([first, second])
        self.assertEqual(first.raynoor_customer_status, "New")
        self.assertEqual(second.raynoor_customer_status, "Returning")
        self.assertEqual(first.raynoor_origin_label, "TikTok")
        self.assertEqual(second.raynoor_origin_label, "WhatsApp")

    def test_same_name_different_saved_customer_records_not_assumed_same_person(self):
        a = EggPOSCustomer.objects.create(name="Ali")
        b = EggPOSCustomer.objects.create(name="Ali")
        first, second = decorate_sales([self.sale(customer=a), self.sale(customer=b)])
        self.assertEqual(first.raynoor_customer_status, "New")
        self.assertEqual(second.raynoor_customer_status, "New")

    def test_reversed_sale_ignored_for_customer_history(self):
        customer = EggPOSCustomer.objects.create(name="Sara")
        reversed_sale = self.sale(customer=customer)
        reversed_sale.is_reversed = True
        reversed_sale.save(update_fields=["is_reversed"])
        valid_sale = self.sale(customer=customer)
        reversed_sale, valid_sale = decorate_sales([reversed_sale, valid_sale])
        self.assertEqual(reversed_sale.raynoor_customer_status, "Reversed")
        self.assertEqual(valid_sale.raynoor_customer_status, "New")
