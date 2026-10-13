"""Run: python manage.py test api.online_image_display_tests"""
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from api.models.egg_pos import EggPOSProduct, EggPOSSupplier, EggPOSInventoryLot
from api.models.online_shop import OnlineOffer


class OnlineImageDisplayTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_superuser('image_fit_owner', 'image-fit@example.test', 'image-fit-password')
        cls.product = EggPOSProduct.objects.create(name='Farm Brown Eggs', sku='FIT-EGG-01', default_sale_price=Decimal('25'))
        supplier = EggPOSSupplier.objects.create(name='Image Fit Supplier')
        EggPOSInventoryLot.objects.create(product=cls.product, supplier=supplier, source_type='purchase',
            lot_code='FIT-EGG-LOT', quantity_received=150, quantity_remaining=150, unit_cost=Decimal('14'))

    def test_default_fit_shows_whole_photo_and_changes_do_not_touch_stock(self):
        offer = OnlineOffer.objects.create(product=self.product, pack_size=30, price_per_pack=Decimal('840'), is_active=True,
            photo_mime='image/jpeg', photo_data=b'jpeg-testing-bytes')
        self.assertEqual(offer.photo_fit, 'contain')
        self.assertContains(self.client.get(reverse('raynoor_store')), 'photo-contain')
        self.client.force_login(self.owner)
        response = self.client.post(reverse('raynoor_shop_setup'), {
            'kind':'offer', 'object_id':str(offer.pk), 'product':str(self.product.pk), 'pack_size':'30',
            'price_per_pack':'840', 'photo_fit':'cover', 'photo_position':'top',
            'photo_alt':'Full tray of RayNoor brown eggs', 'is_active':'on', 'sort_order':'0'})
        self.assertEqual(response.status_code, 302)
        offer.refresh_from_db()
        self.assertEqual((offer.photo_fit, offer.photo_position, offer.photo_alt),
                         ('cover','top','Full tray of RayNoor brown eggs'))
        self.assertEqual(EggPOSInventoryLot.objects.get(lot_code='FIT-EGG-LOT').quantity_remaining, 150)
        store = self.client.get(reverse('raynoor_store'))
        self.assertContains(store, 'photo-cover')
        self.assertContains(store, 'object-position:top')
        self.assertContains(store, 'alt="Full tray of RayNoor brown eggs"')

    def test_alt_is_escaped_and_unknown_fit_rejected(self):
        self.client.force_login(self.owner)
        data = {'kind':'offer', 'product':str(self.product.pk), 'pack_size':'12',
                'price_per_pack':'400', 'photo_fit':'contain', 'photo_position':'center',
                'photo_alt':'Eggs <script>alert(1)</script>', 'is_active':'on', 'sort_order':'0'}
        response=self.client.post(reverse('raynoor_shop_setup'), data)
        self.assertEqual(response.status_code,302)
        offer=OnlineOffer.objects.get()
        offer.photo_mime='image/jpeg';offer.photo_data=b'jpeg-for-html-test';offer.save(update_fields=['photo_mime','photo_data'])
        store=self.client.get(reverse('raynoor_store'))
        self.assertContains(store,'Eggs &lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(store,'alt="Eggs <script>')
        data['photo_fit']='unexpected; background:url(unsafe)'
        data['object_id']=str(offer.pk)
        response=self.client.post(reverse('raynoor_shop_setup'),data)
        self.assertEqual(response.status_code,200)
        offer.refresh_from_db()
        self.assertEqual(offer.photo_fit,'contain')
