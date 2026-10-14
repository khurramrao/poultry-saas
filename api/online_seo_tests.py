"""Run with: python manage.py test api.online_seo_tests"""
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from api.models.egg_pos import EggPOSProduct
from api.models.online_shop import OnlineOffer
from api.templatetags.product_rich_text import rich_product_description


class ProductSeoAndFormattingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(username='seo_admin', password='SeoAdminStrong8581', email='test@example.org')
        cls.visitor = get_user_model().objects.create_user(username='seo_visitor', password='LongSafePassword919')
        cls.product = EggPOSProduct.objects.create(name='Organic Egg', sku='SEO-ORGANIC', default_sale_price=Decimal('30'))
        cls.offer = OnlineOffer.objects.create(product=cls.product, pack_size=6, title='RayNoor Farm Fresh Eggs — 6 Pack', price_per_pack=Decimal('250'), is_active=True,
            description='## Product Highlights\n- **6 eggs** per pack\n- Carefully packed for Lahore delivery\n\n*Fresh eggs* for daily meals.',
            seo_title='Fresh Eggs 6 Pack in Lahore | RayNoor Farms', seo_description='Buy a pack of 6 fresh chicken eggs online from RayNoor Farms. Delivery in Lahore.')

    def url(self):
        return reverse('raynoor_product_detail', kwargs={'offer_id':self.offer.pk})

    def test_seo_title_meta_and_canonical(self):
        r=self.client.get(self.url())
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, '<title>Fresh Eggs 6 Pack in Lahore | RayNoor Farms</title>', html=False)
        self.assertContains(r, 'content="Buy a pack of 6 fresh chicken eggs online from RayNoor Farms. Delivery in Lahore."', html=False)
        self.assertContains(r, 'rel="canonical"', html=False)

    def test_safe_formatted_description(self):
        r=self.client.get(self.url())
        self.assertContains(r,'<h2>Product Highlights</h2>',html=False)
        self.assertContains(r,'<strong>6 eggs</strong>',html=False)
        self.assertContains(r,'<li>Carefully packed for Lahore delivery</li>',html=False)
        self.assertContains(r,'<em>Fresh eggs</em>',html=False)
        self.assertNotContains(r,'<script>alert(', html=False)

    def test_escaping_untrusted_html_in_product_description(self):
        self.offer.description='## <script>alert(1)</script>\n- **<img src=x onerror=alert(1)>**'
        self.offer.save(update_fields=['description'])
        r=self.client.get(self.url())
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r,'<script>alert(1)</script>',html=False)
        self.assertNotContains(r,'<img src=x',html=False)
        self.assertContains(r,'&lt;script&gt;',html=False)
        self.assertContains(r,'&lt;img',html=False)

    def test_empty_seo_fallback(self):
        self.offer.seo_title='';self.offer.seo_description='';self.offer.save(update_fields=['seo_title','seo_description'])
        r=self.client.get(self.url());self.assertEqual(r.status_code,200)
        self.assertContains(r,'RayNoor Farm Fresh Eggs — 6 Pack | RayNoor Farms Lahore')
        self.assertContains(r,'Product Highlights',html=False)

    def test_setup_saves_fields_without_affecting_pos_inventory(self):
        self.client.force_login(self.admin)
        fields={'kind':'offer','object_id':str(self.offer.pk),'product':str(self.product.pk),'pack_size':'6','title':self.offer.title,
                'description':'## Fresh Eggs\n- **6** per pack','seo_title':'Fresh Eggs | Lahore Delivery','seo_description':'Fresh eggs available for delivery in Lahore.',
                'price_per_pack':'250','photo_fit':'contain','photo_position':'center','sort_order':'0','is_active':'on'}
        r=self.client.post(reverse('raynoor_shop_setup'),fields)
        self.assertEqual(r.status_code,302)
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.seo_title,'Fresh Eggs | Lahore Delivery')
        self.assertEqual(self.offer.seo_description,'Fresh eggs available for delivery in Lahore.')
        self.assertEqual(self.offer.price_per_pack,Decimal('250'))
        self.assertEqual(self.offer.description,'## Fresh Eggs\n- **6** per pack')

    def test_non_admin_cannot_update_seo(self):
        self.client.force_login(self.visitor)
        r=self.client.post(reverse('raynoor_shop_setup'),{'kind':'offer','object_id':str(self.offer.pk),'seo_title':'Hacked'})
        self.assertEqual(r.status_code,404)
        self.offer.refresh_from_db()
        self.assertNotEqual(self.offer.seo_title,'Hacked')
