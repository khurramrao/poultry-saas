"""python manage.py test api.online_shop_setup_tests"""
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from api.models.egg_pos import EggPOSProduct, EggPOSSupplier, EggPOSInventoryLot
from api.models.online_shop import OnlineOffer, OnlineDeliveryZone


class OwnerShopSetupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_superuser(
            username='shop_setup_owner', email='shop-owner@example.test', password='test-owner-secret-512')
        cls.staff = get_user_model().objects.create_user(
            username='shop_setup_staff', password='test-staff-secret-512', is_staff=True)
        cls.product = EggPOSProduct.objects.create(name='Shop setup egg 60+', sku='SHOP-SETUP-60', default_sale_price=Decimal('18'))
        cls.supplier = EggPOSSupplier.objects.create(name='Setup supplier')
        EggPOSInventoryLot.objects.create(product=cls.product, supplier=cls.supplier,
            source_type='purchase', lot_code='SHOP-SETUP-LOT', quantity_received=48,
            quantity_remaining=48, unit_cost=Decimal('10'))

    def test_shop_home_button(self):
        response = self.client.get(reverse('raynoor_store'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'nav-home')
        self.assertContains(response, 'Home</a>')

    def test_shop_setup_is_not_accessible_to_public_or_staff(self):
        url = reverse('raynoor_shop_setup')
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, {'kind': 'zone', 'name':'DHA','delivery_fee':'100'}).status_code, 404)
        self.assertFalse(OnlineDeliveryZone.objects.exists())

    def test_owner_creates_pack_and_lahore_area_then_store_displays_product(self):
        self.client.force_login(self.owner)
        url = reverse('raynoor_shop_setup')
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Create an egg pack')
        response = self.client.post(url, {'kind': 'offer', 'product':str(self.product.pk), 'pack_size':'12',
            'title': 'White Eggs · Dozen', 'price_per_pack':'300', 'sort_order':'1', 'is_active':'on'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(OnlineOffer.objects.count(), 1)
        self.assertTrue(OnlineOffer.objects.first().is_active)
        response = self.client.post(url, {'kind':'zone','name':'Johar Town',
            'delivery_fee':'120', 'minimum_order':'250', 'own_rider_allowed':'on', 'is_active':'on'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(OnlineDeliveryZone.objects.filter(is_active=True).count(), 1)
        shop = self.client.get(reverse('raynoor_store'))
        self.assertContains(shop, 'White Eggs · Dozen')
        self.assertNotContains(shop, 'Our fresh egg packs are coming soon')
        self.assertContains(shop, '300')

    def test_edit_pack_and_deactivate_while_preserving_stock(self):
        self.client.force_login(self.owner)
        offer = OnlineOffer.objects.create(product=self.product, pack_size=12, price_per_pack=Decimal('220'), is_active=True)
        url = reverse('raynoor_shop_setup')
        response = self.client.get(f'{url}?offer={offer.pk}')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Edit egg pack')
        response = self.client.post(url, {'kind':'offer','object_id':str(offer.pk),
            'product':str(self.product.pk),'pack_size':'12', 'price_per_pack':'250','sort_order':'0'})
        self.assertEqual(response.status_code, 302)
        offer.refresh_from_db()
        self.assertEqual(offer.price_per_pack, Decimal('250'))
        self.assertFalse(offer.is_active)
        self.assertEqual(EggPOSInventoryLot.objects.first().quantity_remaining, 48)
        self.assertNotContains(self.client.get(reverse('raynoor_store')), 'Shop setup egg 60+')

    def test_reject_duplicate_pack_and_active_area_without_rider(self):
        self.client.force_login(self.owner)
        url = reverse('raynoor_shop_setup')
        OnlineOffer.objects.create(product=self.product, pack_size=12, price_per_pack=Decimal('250'), is_active=True)
        response = self.client.post(url, {'kind':'offer', 'product':str(self.product.pk),
            'pack_size':'12', 'price_per_pack':'200', 'sort_order':'0','is_active':'on'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(OnlineOffer.objects.count(), 1)
        response = self.client.post(url, {'kind':'zone','name':'Gulberg', 'delivery_fee':'120',
            'minimum_order':'0','is_active':'on'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(OnlineDeliveryZone.objects.exists())
