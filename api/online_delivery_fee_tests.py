"""Checks pre-invoice customer delivery fee adjustments. Run: python manage.py test api.online_delivery_fee_tests"""
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse

from api.models.egg_pos import EggPOSProduct, EggPOSSupplier, EggPOSInventoryLot, EggPOSSale, EggPOSSalePayment
from api.models.online_shop import OnlineDeliveryZone, OnlineOffer, OnlineOrder, OnlineOrderEvent
from api.services.online_shop import checkout_order, action_order


class CustomerDeliveryFeeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(
            username='delivery_fee_admin', email='admin@example.test', password='Only-For-Test-1234')
        cls.other = get_user_model().objects.create_user(username='fee_regular_user', password='Another-Test-1234')
        cls.product = EggPOSProduct.objects.create(name='Test eggs', sku='FEE-TEST', default_sale_price=Decimal('20'))
        cls.supplier = EggPOSSupplier.objects.create(name='Fee Test Supplier')
        cls.lot = EggPOSInventoryLot.objects.create(product=cls.product, supplier=cls.supplier,
            source_type='purchase', lot_code='FEE-TEST-LOT', quantity_received=200,
            quantity_remaining=200, unit_cost=Decimal('14'))
        cls.offer = OnlineOffer.objects.create(product=cls.product, pack_size=12,
            price_per_pack=Decimal('430'), is_active=True)
        cls.zone = OnlineDeliveryZone.objects.create(name='Pickup Test Lahore', delivery_fee=Decimal('250'),
            minimum_order=Decimal('100'), is_active=True)

    def make_order(self):
        return checkout_order({str(self.offer.id): 3}, account=None, name='Test Customer',
            phone='03001234567', address='Sample Lahore address', zone=self.zone, note='Customer pickup')

    def test_waive_fee_from_packed_order_updates_customer_document_without_creating_sale(self):
        order = self.make_order()
        action_order(order.pk, self.admin, 'confirm')
        action_order(order.pk, self.admin, 'pack')
        self.assertEqual(order.subtotal, Decimal('1290.00'))
        admin_client = Client()
        admin_client.force_login(self.admin)
        page = admin_client.get(reverse('raynoor_admin_order_detail', kwargs={'order_id':order.pk}))
        self.assertContains(page, 'Customer delivery fee')
        url = reverse('raynoor_admin_order_action', kwargs={'order_id':order.pk})
        result = admin_client.post(url, {'action':'change_fee', 'customer_delivery_fee':'0',
            'reason':'Customer pickup — no delivery', 'admin_password':'Only-For-Test-1234'})
        self.assertEqual(result.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.delivery_fee, Decimal('0.00'))
        self.assertEqual(order.total, Decimal('1290.00'))
        self.assertEqual(order.delivery_cost, Decimal('0.00'))
        self.assertEqual(order.status, OnlineOrder.PACKED)
        self.assertEqual(EggPOSSale.objects.count(), 0)
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantity_remaining, 200)
        self.assertEqual(OnlineOrderEvent.objects.filter(order=order, event='change_fee').count(), 1)
        updated_confirmation = self.client.get(reverse('raynoor_order_document', kwargs={'token':order.token}))
        self.assertContains(updated_confirmation, '1,290')
        self.assertNotContains(updated_confirmation, '1,540')

        # Customer pickup without a delivery charge; POS invoice is generated only after confirmation.
        action_order(order.pk, self.admin, 'dispatch', delivery_method='own',
            rider_name='Customer pickup', actual_cost='0')
        finalized = action_order(order.pk, self.admin, 'deliver')
        self.assertEqual(finalized.total, Decimal('1290.00'))
        sale = EggPOSSale.objects.get(pk=finalized.pos_sale_id)
        self.assertEqual(sale.net_total, Decimal('1290.00'))
        self.assertEqual(sale.payments.count(), 0)

    def test_requires_admin_password_and_valid_reason(self):
        order = self.make_order()
        admin_client = Client()
        admin_client.force_login(self.admin)
        url = reverse('raynoor_admin_order_action', kwargs={'order_id':order.pk})
        admin_client.post(url, {'action':'change_fee', 'customer_delivery_fee':'0',
            'reason':'Customer pickup', 'admin_password':'wrong'})
        order.refresh_from_db()
        self.assertEqual(order.total, Decimal('1540.00'))
        admin_client.post(url, {'action':'change_fee', 'customer_delivery_fee':'0',
            'reason':'', 'admin_password':'Only-For-Test-1234'})
        order.refresh_from_db()
        self.assertEqual(order.delivery_fee, Decimal('250.00'))
        staff_client = Client()
        staff_client.force_login(self.other)
        self.assertEqual(staff_client.post(url, {'action':'change_fee',
            'customer_delivery_fee':'0','reason':'Pickup', 'admin_password':'Another-Test-1234'}).status_code, 404)
        order.refresh_from_db()
        self.assertEqual(order.total, Decimal('1540.00'))
        self.assertFalse(OnlineOrderEvent.objects.filter(order=order, event='change_fee').exists())

    def test_cannot_change_fee_after_final_pos_invoice(self):
        order = self.make_order()
        action_order(order.pk, self.admin, 'confirm')
        action_order(order.pk, self.admin, 'pack')
        action_order(order.pk, self.admin, 'dispatch', delivery_method='own',
            rider_name='RayNoor Rider', actual_cost='100')
        finalized = action_order(order.pk, self.admin, 'deliver')
        with self.assertRaises(ValueError):
            action_order(order.pk, self.admin, 'change_fee', customer_delivery_fee='0', reason='Free delivery')
        finalized.refresh_from_db()
        self.assertEqual(finalized.total, Decimal('1540.00'))
        self.assertEqual(finalized.pos_sale.net_total, Decimal('1540.00'))
        self.assertEqual(EggPOSSale.objects.count(), 1)
