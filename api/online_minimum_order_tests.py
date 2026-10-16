"""Run: python manage.py test api.online_minimum_order_tests"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from api.models.egg_pos import EggPOSInventoryLot, EggPOSProduct, EggPOSSupplier
from api.models.online_shop import OnlineDeliveryZone, OnlineOffer, OnlineOrder
from api.services.online_shop import MIN_EGGS_SUBTOTAL, checkout_order


class RayNoorMinimumEggOrderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.product = EggPOSProduct.objects.create(
            name='Test Brown Eggs', sku='RNET-MIN-001',
            default_sale_price=Decimal('30.00'),
        )
        cls.supplier = EggPOSSupplier.objects.create(name='Test Minimum Supplier')
        EggPOSInventoryLot.objects.create(
            product=cls.product, supplier=cls.supplier, source_type='purchase',
            lot_code='MIN-ORDER-001', quantity_received=500,
            quantity_remaining=500, unit_cost=Decimal('15.00'),
        )
        cls.offer = OnlineOffer.objects.create(
            product=cls.product, pack_size=12,
            price_per_pack=Decimal('250.00'), is_active=True,
        )
        cls.zone = OnlineDeliveryZone.objects.create(
            name='Test Lahore Zone', delivery_fee=Decimal('250.00'),
            minimum_order=Decimal('0.00'), is_active=True,
        )

    def order(self, count):
        return checkout_order(
            {str(self.offer.pk): count}, account=None,
            name='RayNoor Buyer', phone='03001234567',
            address='Lahore Pakistan, test address', zone=self.zone, note='',
        )

    def test_delivery_does_not_qualify_for_minimum(self):
        self.assertEqual(MIN_EGGS_SUBTOTAL, Decimal('1000.00'))
        # Rs 750 in eggs plus Rs 250 delivery still MUST be rejected.
        with self.assertRaisesRegex(ValueError, 'Add Rs 250 more'):
            self.order(3)
        self.assertFalse(OnlineOrder.objects.exists())

    def test_exactly_1000_eggs_subtotal_is_valid(self):
        order = self.order(4)
        self.assertEqual(order.subtotal, Decimal('1000.00'))
        self.assertEqual(order.delivery_fee, Decimal('250.00'))
        self.assertEqual(order.total, Decimal('1250.00'))

    def test_higher_delivery_zone_minimum_is_kept(self):
        self.zone.minimum_order = Decimal('1500.00')
        self.zone.save(update_fields=['minimum_order'])
        with self.assertRaisesRegex(ValueError, 'Add Rs 500 more'):
            self.order(4)
        order = self.order(6)
        self.assertEqual(order.subtotal, Decimal('1500.00'))

    def test_notice_is_on_shop_cart_checkout_and_product(self):
        shop = self.client.get(reverse('raynoor_store'))
        self.assertEqual(shop.status_code, 200)
        self.assertContains(shop, 'Minimum egg order: Rs 1,000')
        self.assertContains(shop, 'Minimum order: Rs 1,000 in eggs')
        product = self.client.get(reverse('raynoor_product_detail', kwargs={'offer_id': self.offer.pk}))
        self.assertEqual(product.status_code, 200)
        self.assertContains(product, 'Minimum order: Rs 1,000 in eggs')
        self.client.post(reverse('raynoor_cart_update'), {'offer_id': self.offer.pk, 'count': 3})
        cart = self.client.get(reverse('raynoor_cart'))
        self.assertContains(cart, 'Add more eggs to cart')
        checkout = self.client.get(reverse('raynoor_checkout'))
        self.assertContains(checkout, 'Minimum egg subtotal: Rs 1,000')
        response = self.client.post(reverse('raynoor_checkout'), {
            'name': 'Buyer', 'phone': '03001234567',
            'zone_id': str(self.zone.pk), 'address': 'Lahore, Test Address',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(OnlineOrder.objects.exists())
