"""Run: python manage.py test api.online_offer_photo_tests"""
from io import BytesIO
from decimal import Decimal
from PIL import Image
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from api.models.egg_pos import EggPOSProduct, EggPOSSupplier, EggPOSInventoryLot
from api.models.online_shop import OnlineOffer


def jpeg_upload(color='gold', filename='eggs.jpg'):
    canvas = Image.new('RGB', (200, 120), color=color)
    buf = BytesIO()
    canvas.save(buf, format='JPEG')
    return SimpleUploadedFile(filename, buf.getvalue(), content_type='image/jpeg')


class OnlineOfferPhotoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_superuser('photo_owner', 'photo@example.test', 'good-photo-password')
        cls.employee = get_user_model().objects.create_user('photo_employee', password='employee-password', is_staff=True)
        cls.product = EggPOSProduct.objects.create(name='White Eggs', sku='PHOTO-TEST-01', default_sale_price=Decimal('20'))
        cls.supplier = EggPOSSupplier.objects.create(name='Photo supplier')
        cls.lot = EggPOSInventoryLot.objects.create(product=cls.product, supplier=cls.supplier,
            source_type='purchase', lot_code='PHOTO-LOT', quantity_received=90,
            quantity_remaining=90, unit_cost=Decimal('14'))

    def save_offer(self, photo=None, pk=None, extra=None):
        data = {'kind':'offer', 'object_id':str(pk or ''), 'product':str(self.product.pk),
                'pack_size':'12', 'title':'Fresh White Dozen', 'price_per_pack':'420',
                'sort_order':'1', 'is_active':'on'}
        if extra: data.update(extra)
        if photo: data['photo_upload'] = photo
        return self.client.post(reverse('raynoor_shop_setup'), data)

    def test_photo_upload_served_to_public_and_persists_database(self):
        self.client.force_login(self.owner)
        result = self.save_offer(photo=jpeg_upload())
        self.assertEqual(result.status_code, 302)
        offer = OnlineOffer.objects.get()
        self.assertEqual(offer.photo_mime, 'image/jpeg')
        self.assertTrue(offer.photo_updated_at)
        self.assertTrue(bytes(offer.photo_data).startswith(b'\xff\xd8'))
        self.assertIn('multipart/form-data', self.client.get(reverse('raynoor_shop_setup')).content.decode())
        self.client.logout()
        store = self.client.get(reverse('raynoor_store'))
        self.assertContains(store, reverse('raynoor_offer_photo', args=[offer.pk]))
        photo = self.client.get(reverse('raynoor_offer_photo', args=[offer.pk]))
        self.assertEqual(photo.status_code, 200)
        self.assertEqual(photo['Content-Type'], 'image/jpeg')
        with Image.open(BytesIO(photo.content)) as image:
            self.assertEqual(image.format, 'JPEG')
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantity_remaining, 90)

    def test_edit_without_upload_preserves_existing_photo_then_removes(self):
        self.client.force_login(self.owner)
        self.save_offer(photo=jpeg_upload())
        offer = OnlineOffer.objects.get()
        original = bytes(offer.photo_data)
        self.save_offer(pk=offer.pk, extra={'title':'Better Dozen'})
        offer.refresh_from_db()
        self.assertEqual(bytes(offer.photo_data), original)
        self.save_offer(pk=offer.pk, extra={'remove_photo':'on'})
        offer.refresh_from_db()
        self.assertFalse(offer.has_photo)
        self.assertIsNone(offer.photo_data)
        self.assertEqual(self.client.get(reverse('raynoor_offer_photo', args=[offer.pk])).status_code,404)

    def test_rejects_invalid_file_and_does_not_modify_data(self):
        self.client.force_login(self.owner)
        bad = SimpleUploadedFile('not-image.jpg', b'not really an image', content_type='image/jpeg')
        result = self.save_offer(photo=bad)
        self.assertEqual(result.status_code, 200)
        self.assertContains(result, 'image could not be read')
        self.assertFalse(OnlineOffer.objects.exists())

    def test_rejects_oversized_upload(self):
        self.client.force_login(self.owner)
        bad = SimpleUploadedFile('too-big.jpg', b'0' * (4 * 1024 * 1024 + 1), content_type='image/jpeg')
        result = self.save_offer(photo=bad)
        self.assertEqual(result.status_code, 200)
        self.assertContains(result, '4 MB')
        self.assertFalse(OnlineOffer.objects.exists())

    def test_draft_images_are_not_public(self):
        self.client.force_login(self.owner)
        self.save_offer(photo=jpeg_upload(), extra={'is_active': ''})
        offer = OnlineOffer.objects.get()
        self.assertFalse(offer.is_active)
        url = reverse('raynoor_offer_photo', args=[offer.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.employee)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_cart_uses_photo_when_pack_available(self):
        self.client.force_login(self.owner)
        self.save_offer(photo=jpeg_upload())
        offer = OnlineOffer.objects.get()
        self.client.logout()
        self.client.post(reverse('raynoor_cart_update'), {'offer_id':offer.pk,'count':1})
        cart = self.client.get(reverse('raynoor_cart'))
        self.assertContains(cart, reverse('raynoor_offer_photo', args=[offer.pk]))

    def test_transparent_png_is_normalized_to_jpeg(self):
        canvas = Image.new('RGBA', (160, 90), (0, 0, 0, 0))
        buffer = BytesIO()
        canvas.save(buffer, format='PNG')
        self.client.force_login(self.owner)
        image = SimpleUploadedFile('transparent.png', buffer.getvalue(), content_type='image/png')
        result = self.save_offer(photo=image)
        self.assertEqual(result.status_code, 302)
        photo = bytes(OnlineOffer.objects.get().photo_data)
        with Image.open(BytesIO(photo)) as output:
            self.assertEqual(output.mode, 'RGB')
            self.assertEqual(output.format, 'JPEG')

    def test_django_admin_shows_photo_upload_field(self):
        offer = OnlineOffer.objects.create(product=self.product, pack_size=12,
            price_per_pack=Decimal('250'), is_active=True)
        self.client.force_login(self.owner)
        response = self.client.get(reverse('admin:api_onlineoffer_change', args=[offer.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'photo_upload')

    def test_django_admin_accepts_photo_upload(self):
        offer = OnlineOffer.objects.create(product=self.product, pack_size=12,
            price_per_pack=Decimal('250'), is_active=True)
        self.client.force_login(self.owner)
        url = reverse('admin:api_onlineoffer_change', args=[offer.pk])
        response = self.client.post(url, {
            'product': str(self.product.pk), 'pack_size':'12',
            'title': 'Retail 12 eggs', 'price_per_pack':'270',
            'sort_order':'0', 'is_active':'on', 'photo_upload': jpeg_upload(),
            '_save':'Save',
        })
        self.assertEqual(response.status_code, 302)
        offer.refresh_from_db()
        self.assertTrue(offer.has_photo)
        self.assertEqual(offer.price_per_pack, Decimal('270'))

    def test_staff_cannot_upload_photo(self):
        self.client.force_login(self.employee)
        response = self.save_offer(photo=jpeg_upload())
        self.assertEqual(response.status_code,404)
        self.assertFalse(OnlineOffer.objects.exists())
