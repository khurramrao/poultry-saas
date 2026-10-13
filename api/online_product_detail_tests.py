"""Run with: python manage.py test api.online_product_detail_tests"""
from decimal import Decimal
from io import BytesIO
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from api.models.egg_pos import EggPOSProduct, EggPOSSupplier, EggPOSInventoryLot
from api.models.online_shop import OnlineOffer, OnlineOfferGalleryPhoto


def image_upload(filename='tray.png'):
    img = Image.new('RGB', (300, 240), '#d9b887')
    stream = BytesIO()
    img.save(stream, format='PNG')
    return SimpleUploadedFile(filename, stream.getvalue(), content_type='image/png')


class PublicProductDetailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_superuser(
            username='detail_test_owner', email='detail@example.test', password='StrongSecret97851')
        cls.staff = get_user_model().objects.create_user(
            username='detail_test_staff', password='StrongSecret97851', is_staff=True)
        cls.product = EggPOSProduct.objects.create(
            name='Organic Eggs', sku='DETAIL-ORGANIC', default_sale_price=Decimal('30'))
        supplier = EggPOSSupplier.objects.create(name='Detail supplier')
        EggPOSInventoryLot.objects.create(
            product=cls.product, supplier=supplier, source_type='purchase',
            lot_code='DETAIL-001', quantity_received=120, quantity_remaining=120,
            unit_cost=Decimal('20'))
        cls.offer = OnlineOffer.objects.create(
            product=cls.product, pack_size=12, price_per_pack=Decimal('430'),
            title='RayNoor Farm Fresh Eggs — 12 Pack', is_active=True,
            description='Real eggs raised at our farm.\nCarefully packed for delivery.',
            egg_weight_label='55–60 g per egg',
        )
        cls.other = OnlineOffer.objects.create(
            product=cls.product, pack_size=30, price_per_pack=Decimal('960'),
            title='RayNoor Farm Fresh Eggs — 30-Egg Tray', is_active=True,
        )

    def offer_data(self, **updates):
        data = {
            'kind': 'offer', 'object_id': str(self.offer.pk), 'product':str(self.product.pk),
            'pack_size':'12', 'title':self.offer.title, 'description':self.offer.description,
            'egg_weight_label':self.offer.egg_weight_label, 'price_per_pack':'430',
            'photo_fit':'contain', 'photo_position':'center', 'sort_order':'0', 'is_active':'on',
        }
        data.update(updates)
        return data

    def test_card_links_to_public_product_page(self):
        response=self.client.get(reverse('raynoor_store'))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,reverse('raynoor_product_detail',kwargs={'offer_id':self.offer.pk}))
        page=self.client.get(reverse('raynoor_product_detail',kwargs={'offer_id':self.offer.pk}))
        self.assertEqual(page.status_code,200)
        self.assertContains(page,'Rs 430')
        self.assertContains(page,'55–60 g per egg')
        self.assertContains(page,'Other pack sizes')
        self.assertContains(page,'30 eggs · Rs 960')
        self.assertContains(page,'Carefully packed for delivery.')

    def test_draft_is_not_public(self):
        self.offer.is_active=False
        self.offer.save(update_fields=['is_active'])
        self.assertEqual(self.client.get(reverse('raynoor_product_detail',kwargs={'offer_id':self.offer.pk})).status_code,404)

    def test_existing_add_to_cart_flow(self):
        page=self.client.get(reverse('raynoor_product_detail',kwargs={'offer_id':self.offer.pk}))
        self.assertContains(page,'name="offer_id"')
        response=self.client.post(reverse('raynoor_cart_update'),{'offer_id':self.offer.pk,'count':2})
        self.assertRedirects(response,reverse('raynoor_cart'))
        self.assertEqual(self.client.session['raynoor_cart'][str(self.offer.pk)],2)
        self.assertEqual(EggPOSInventoryLot.objects.get(lot_code='DETAIL-001').quantity_remaining,120)

    def test_setup_gallery_upload_and_public_gallery_response(self):
        self.client.force_login(self.owner)
        response=self.client.post(reverse('raynoor_shop_setup'),
            self.offer_data(photo_upload=image_upload('main.png'),gallery_photos=[image_upload('side.png'),image_upload('package.png')]))
        self.assertEqual(response.status_code,302)
        self.assertEqual(OnlineOfferGalleryPhoto.objects.filter(offer=self.offer).count(),2)
        self.offer.refresh_from_db()
        self.assertTrue(self.offer.has_photo)
        self.client.logout()
        image=OnlineOfferGalleryPhoto.objects.filter(offer=self.offer).first()
        result=self.client.get(reverse('raynoor_offer_gallery_photo',kwargs={'photo_id':image.pk}))
        self.assertEqual(result.status_code,200)
        self.assertEqual(result['Content-Type'],'image/jpeg')
        self.assertTrue(result.content.startswith(b'\xff\xd8'))
        page=self.client.get(reverse('raynoor_product_detail',kwargs={'offer_id':self.offer.pk}))
        self.assertContains(page,reverse('raynoor_offer_gallery_photo',kwargs={'photo_id':image.pk}))

    def test_gallery_limit_and_security(self):
        self.client.force_login(self.owner)
        response=self.client.post(reverse('raynoor_shop_setup'),self.offer_data(gallery_photos=[image_upload(f'g{i}.png') for i in range(5)]))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'at most four gallery photos')
        self.assertEqual(self.offer.gallery_photos.count(),0)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.post(reverse('raynoor_shop_setup'),self.offer_data(gallery_photos=[image_upload()])).status_code,404)
        self.assertEqual(self.offer.gallery_photos.count(),0)

    def test_gallery_remove_and_draft_photo_private(self):
        image=OnlineOfferGalleryPhoto.objects.create(offer=self.offer,photo_data=b'\xff\xd8demo',alt_text='Side view')
        self.client.force_login(self.owner)
        result=self.client.post(reverse('raynoor_shop_setup'),self.offer_data(remove_gallery=[str(image.pk)]))
        self.assertEqual(result.status_code,302)
        self.assertFalse(OnlineOfferGalleryPhoto.objects.filter(pk=image.pk).exists())
        image=OnlineOfferGalleryPhoto.objects.create(offer=self.offer,photo_data=b'\xff\xd8demo')
        self.offer.is_active=False
        self.offer.save(update_fields=['is_active'])
        self.client.logout()
        self.assertEqual(self.client.get(reverse('raynoor_offer_gallery_photo',kwargs={'photo_id':image.pk})).status_code,404)

    def test_plain_text_details_are_escaped(self):
        self.offer.description='<script>alert("x")</script>'
        self.offer.save(update_fields=['description'])
        response=self.client.get(reverse('raynoor_product_detail',kwargs={'offer_id':self.offer.pk}))
        self.assertNotContains(response,'<script>alert("x")</script>')
        self.assertContains(response,'&lt;script&gt;',html=False)
