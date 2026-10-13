"""Run with: python manage.py test api.online_tracking_tests"""
from decimal import Decimal
from django.test import Client, TestCase
from django.core.cache import cache
from django.urls import reverse
from api.models.egg_pos import EggPOSProduct, EggPOSInventoryLot, EggPOSSupplier
from api.models.online_shop import OnlineOffer, OnlineDeliveryZone, OnlineOrder
from api.services.online_shop import action_order, checkout_order
from django.contrib.auth import get_user_model


class GuestOrderTrackingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(
            'order_tracking_admin', 'tracking@example.test', 'a-strong-password-2026!')
        cls.product = EggPOSProduct.objects.create(name='Tracking eggs', sku='TRK-605', default_sale_price=Decimal('25'))
        supplier = EggPOSSupplier.objects.create(name='Tracking supplier')
        EggPOSInventoryLot.objects.create(product=cls.product, supplier=supplier,
            source_type='purchase', lot_code='TRK-LOT', quantity_received=300,
            quantity_remaining=300, unit_cost=Decimal('17'))
        cls.offer = OnlineOffer.objects.create(product=cls.product, pack_size=12,
            price_per_pack=Decimal('360'), is_active=True)
        cls.zone = OnlineDeliveryZone.objects.create(name='Tracking Lahore',
            delivery_fee=Decimal('100'), minimum_order=Decimal('100'), is_active=True)

    def setUp(self):
        cache.clear()

    def _order(self):
        return checkout_order({str(self.offer.pk): 1}, account=None,
            name='Example Buyer', phone='03001234567',
            address='Some private address 123, Lahore', zone=self.zone, note='')

    def test_guest_tracking_has_highlighted_confirmed_step_and_link_copy(self):
        order = self._order()
        response = self.client.get(reverse('raynoor_order_tracking', kwargs={'token': order.token}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Save your private tracking link')
        self.assertContains(response, 'Copy Tracking Link')
        self.assertContains(response, 'tracking-stage is-current')
        self.assertContains(response, 'Order placed')
        action_order(order.pk, self.admin, 'confirm')
        response = self.client.get(reverse('raynoor_order_tracking', kwargs={'token': order.token}))
        html = response.content.decode()
        self.assertIn('Confirmed', html)
        self.assertIn('tracking-stage is-current', html)
        import re
        self.assertRegex(html, re.compile(r'class="tracking-stage is-current" aria-current="step">.*?Confirmed', re.S))
        self.assertIn(order.token, html)
        self.assertEqual(response['Referrer-Policy'], 'no-referrer')
        self.assertIn('no-store', response['Cache-Control'])

    def test_lookup_returns_only_status_and_never_private_link_or_address(self):
        order = self._order()
        url = reverse('raynoor_order_lookup')
        response = Client().post(url, {'order_number': order.order_number.lower(), 'phone': '+923001234567'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, order.order_number)
        self.assertContains(response, 'Awaiting confirmation')
        self.assertNotContains(response, order.token)
        self.assertNotContains(response, 'Some private address 123')
        self.assertNotContains(response, 'View Final POS Invoice')
        self.assertIn('no-store', response['Cache-Control'])

    def test_lookup_rejects_wrong_phone_without_leaking_order(self):
        order = self._order()
        c = Client()
        response = c.post(reverse('raynoor_order_lookup'),
            {'order_number': order.order_number, 'phone': '03009999999'})
        self.assertContains(response, 'No matching order found')
        self.assertNotContains(response, order.token)
        self.assertNotContains(response, 'Some private address 123')

    def test_guest_order_remains_findable_from_nav_in_same_browser(self):
        self.client.post(reverse('raynoor_cart_update'), {'offer_id': self.offer.pk, 'count': 1})
        response = self.client.post(reverse('raynoor_checkout'), {
            'name': 'Example Buyer', 'phone': '03001234567',
            'address': 'Some private address 123, Lahore', 'zone_id': self.zone.pk,
        })
        self.assertEqual(response.status_code, 302)
        order = OnlineOrder.objects.get()
        self.assertEqual(response.url, reverse('raynoor_order_tracking', kwargs={'token': order.token}))
        lookup = self.client.get(reverse('raynoor_order_lookup'))
        self.assertContains(lookup, order.order_number)
        self.assertContains(lookup, reverse('raynoor_order_tracking', kwargs={'token': order.token}))
        store = self.client.get(reverse('raynoor_store'))
        self.assertContains(store, reverse('raynoor_order_lookup'))
        self.assertContains(store, 'Track your delivery without signing in')

    def test_guest_lookup_rate_limit(self):
        order = self._order()
        c = Client()
        url = reverse('raynoor_order_lookup')
        for i in range(8):
            r = c.post(url, {'order_number': order.order_number, 'phone': '03009999999'})
            self.assertContains(r, 'No matching order found')
        r = c.post(url, {'order_number': order.order_number, 'phone': '03001234567'})
        self.assertContains(r, 'Too many attempts')

    def test_cancelled_order_shows_stopped_not_delivered(self):
        order = self._order()
        action_order(order.pk, self.admin, 'cancel', reason='Test cancellation')
        r = self.client.get(reverse('raynoor_order_tracking', kwargs={'token': order.token}))
        self.assertContains(r, 'Cancelled')
        self.assertContains(r, 'tracking-banner is-stopped')
        self.assertNotContains(r, 'Your order has been delivered')
