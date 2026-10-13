"""Run: python manage.py test api.tests.test_online_shop --verbosity 2"""
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from api.models.egg_pos import EggPOSProduct, EggPOSInventoryLot, EggPOSSupplier, EggPOSSale, EggPOSSalePayment
from api.models.online_shop import OnlineOffer, OnlineDeliveryZone, OnlineOrder, OnlineRetailAccount
from api.services.online_shop import checkout_order, action_order, free_available
from api.services.egg_pos import available_product_stock
from api.models.accounting import JournalEntry

class LahoreShopTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(username='online_test_owner', email='owner@example.test', password='safe-password-1234')
        cls.staff = get_user_model().objects.create_user(username='nonadmin_online', password='pw12345')
        cls.product = EggPOSProduct.objects.create(name='White egg 60g', sku='ONLINE-TEST-60', default_sale_price=Decimal('20'))
        cls.supplier = EggPOSSupplier.objects.create(name='Online supplier')
        cls.lot = EggPOSInventoryLot.objects.create(product=cls.product, supplier=cls.supplier, source_type='purchase', lot_code='ONLINE-TEST-LOT', quantity_received=300, quantity_remaining=300, unit_cost=Decimal('14'))
        cls.offer = OnlineOffer.objects.create(product=cls.product, pack_size=12, price_per_pack=Decimal('250'), is_active=True)
        cls.zone = OnlineDeliveryZone.objects.create(name='Test Lahore zone', delivery_fee=Decimal('100'), minimum_order=Decimal('100'), is_active=True)

    def test_public_checkout_reserves_and_fulfillment_posts_separate_delivery_revenue(self):
        c = Client()
        self.assertEqual(c.get(reverse('raynoor_store')).status_code, 200)
        self.assertEqual(c.post(reverse('raynoor_cart_update'), {'offer_id':self.offer.pk,'count':2}).status_code,302)
        self.assertEqual(c.get(reverse('raynoor_cart')).status_code, 200)
        self.assertEqual(c.get(reverse('raynoor_checkout')).status_code, 200)
        response = c.post(reverse('raynoor_checkout'), {'name':'Test Buyer','phone':'03001234567', 'address':'Street 123, Lahore Pakistan','zone_id':self.zone.pk})
        self.assertEqual(response.status_code, 302)
        order = OnlineOrder.objects.get()
        self.assertEqual(order.total, Decimal('600'))
        self.assertEqual(order.items.first().egg_quantity, 24)
        self.assertEqual(order.status, 'reserved')
        self.assertEqual(available_product_stock(self.product), 276)
        self.assertEqual(c.get(reverse('raynoor_order_tracking',kwargs={'token':order.token})).status_code,200)
        self.assertEqual(c.get(reverse('raynoor_admin_orders')).status_code,404)
        self.assertEqual(c.get(reverse('raynoor_admin_order_detail', kwargs={'order_id':order.pk})).status_code, 404)
        admin_client = Client()
        admin_client.force_login(self.admin)
        self.assertEqual(admin_client.get(reverse('raynoor_admin_orders')).status_code, 200)
        self.assertEqual(admin_client.get(reverse('raynoor_admin_order_detail', kwargs={'order_id':order.pk})).status_code, 200)
        action_order(order.pk, self.admin, 'confirm')
        action_order(order.pk, self.admin, 'pack')
        action_order(order.pk, self.admin, 'dispatch', delivery_method='third', rider_name='Test Courier', actual_cost='85')
        order = action_order(order.pk, self.admin, 'deliver')
        self.assertEqual(order.status,'delivered')
        self.assertEqual(available_product_stock(self.product), 276)
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantity_remaining,276)
        sale = EggPOSSale.objects.get(pk=order.pos_sale_id)
        self.assertEqual(sale.net_total, Decimal('600'))
        self.assertEqual(sale.cogs_total, Decimal('336'))
        self.assertEqual(sale.payments.count(),0)
        self.assertEqual(admin_client.get(reverse('egg_pos_sale_invoice', kwargs={'sale_id':sale.pk})).status_code, 200)
        self.assertEqual(admin_client.get(reverse('egg_pos_sale_detail', kwargs={'sale_id':sale.pk})).status_code, 200)
        entry = JournalEntry.objects.get(source_key=f'egg_pos:sale:{sale.pk}')
        self.assertEqual(list(entry.lines.filter(account__code='4000').values_list('credit',flat=True)), [Decimal('500')])
        self.assertEqual(list(entry.lines.filter(account__code='4010').values_list('credit',flat=True)), [Decimal('100')])
        action_order(order.pk, self.admin, 'settle')
        self.assertEqual(EggPOSSalePayment.objects.filter(sale=sale).count(),1)
        with self.assertRaises(ValueError): action_order(order.pk, self.admin, 'settle')

    def test_customer_confirmation_and_final_pos_invoice_are_single_posted_sale(self):
        """Pre-delivery document is non-posting; duplicate deliver requests are idempotent."""
        from api.models.egg_pos import EggPOSSaleAllocation
        from api.models.online_shop import OnlineOrderEvent
        order = checkout_order(
            {str(self.offer.pk): 2}, account=None, name='Lahore Retail Buyer',
            phone='03001234567', address='18 Test Road, Lahore', zone=self.zone,
            note='Please pack carefully',
        )
        original_number = order.order_number
        url = reverse('raynoor_order_document', kwargs={'token': order.token})
        pre = self.client.get(url)
        self.assertEqual(pre.status_code, 200)
        self.assertContains(pre, original_number)
        self.assertContains(pre, 'Order confirmation / Payment request')
        self.assertContains(pre, 'not a tax or final POS invoice')
        self.assertNotContains(pre, 'Final Egg POS invoice:')
        self.assertEqual(pre.headers['X-Robots-Tag'], 'noindex, nofollow, noarchive')
        self.assertIn('no-store', pre.headers['Cache-Control'])
        self.assertEqual(EggPOSSale.objects.count(), 0)
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantity_remaining, 300)
        self.assertEqual(JournalEntry.objects.filter(source_type='egg_pos_sale').count(), 0)

        action_order(order.id, self.admin, 'confirm')
        action_order(order.id, self.admin, 'pack')
        action_order(order.id, self.admin, 'dispatch', delivery_method='own',
                     rider_name='RayNoor rider', actual_cost='90')
        admin_client = Client()
        admin_client.force_login(self.admin)
        detail = admin_client.get(reverse('raynoor_admin_order_detail', kwargs={'order_id': order.id}))
        self.assertContains(detail, 'View / print order confirmation')
        self.assertContains(detail, 'Copy customer invoice link')
        action_url = reverse('raynoor_admin_order_action', kwargs={'order_id': order.id})
        self.assertEqual(admin_client.post(action_url, {'action': 'deliver'}).status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, 'delivered')
        self.assertEqual(order.order_number, original_number)
        sale_id = order.pos_sale_id
        self.assertTrue(sale_id)
        self.assertEqual(EggPOSSale.objects.count(), 1)
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantity_remaining, 276)
        self.assertEqual(EggPOSSaleAllocation.objects.aggregate(s=__import__('django.db.models', fromlist=['Sum']).Sum('quantity'))['s'], 24)
        self.assertEqual(JournalEntry.objects.filter(source_key=f'egg_pos:sale:{sale_id}').count(), 1)
        first_events = OnlineOrderEvent.objects.filter(order=order, event='deliver').count()
        self.assertEqual(first_events, 1)
        # Even if the browser resubmits exactly the same POST, no duplicate sale or COGS.
        self.assertEqual(admin_client.post(action_url, {'action': 'deliver'}).status_code, 302)
        self.assertEqual(action_order(order.id, self.admin, 'deliver').pos_sale_id, sale_id)
        order.refresh_from_db()
        self.assertEqual(order.pos_sale_id, sale_id)
        self.assertEqual(EggPOSSale.objects.count(), 1)
        self.assertEqual(EggPOSSaleAllocation.objects.count(), 1)
        self.assertEqual(OnlineOrderEvent.objects.filter(order=order, event='deliver').count(), first_events)
        self.assertEqual(JournalEntry.objects.filter(source_key=f'egg_pos:sale:{sale_id}').count(), 1)
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantity_remaining, 276)
        final = self.client.get(url)
        self.assertContains(final, original_number)
        self.assertContains(final, 'Final POS Invoice')
        self.assertContains(final, order.pos_sale.sale_number)
        self.assertContains(final, 'Payment pending')
        pos = admin_client.get(reverse('egg_pos_sale_invoice', kwargs={'sale_id': sale_id}))
        self.assertContains(pos, f'Online order reference: {original_number}')
        # Only a separate authenticated COD handover records cash.
        self.assertEqual(EggPOSSalePayment.objects.filter(sale_id=sale_id).count(), 0)
        action_order(order.id, self.admin, 'settle')
        self.assertEqual(EggPOSSalePayment.objects.filter(sale_id=sale_id).count(), 1)
        receipt = self.client.get(url)
        self.assertContains(receipt, 'Paid — thank you')
        self.assertContains(receipt, 'Rs 0.00')

    def test_cancel_releases_stock_without_creating_pos_invoice(self):
        order = checkout_order({str(self.offer.id): 2}, account=None, name='Buyer',phone='03001234567', address='123 Lahore street',zone=self.zone,note='')
        self.assertEqual(free_available(self.product.id),276)
        action_order(order.id,self.admin,'cancel',reason='Customer cancelled')
        self.assertEqual(free_available(self.product.id),300)
        self.assertEqual(EggPOSSale.objects.count(),0)

    def test_active_reservation_prevents_counter_sale_stock_oversell(self):
        order = checkout_order({str(self.offer.pk):2}, account=None,name='Buyer',phone='03001234567',address='123 Lahore street',zone=self.zone,note='')
        self.assertEqual(available_product_stock(self.product),276)
        admin_client = Client()
        admin_client.force_login(self.admin)
        response = admin_client.post(reverse('egg_pos_new_sale'), {
            'sale_date':__import__('django.utils.timezone',fromlist=['localdate']).localdate().isoformat(), 'product_id':str(self.product.pk),
            'quantity':'280','unit_price':'25', 'payment_type':'full','payment_method':'cash',
        })
        self.assertEqual(EggPOSSale.objects.count(), 0)
        self.assertEqual(response.status_code, 200)
        with self.assertRaises(ValueError):
            checkout_order({str(self.offer.pk):24},account=None,name='Buyer2',phone='03001234567',address='456 Lahore street',zone=self.zone,note='')
        self.assertIsNotNone(order)

    def test_account_orders_do_not_claim_guest_by_phone(self):
        guest = checkout_order({str(self.offer.pk):1},account=None,name='Guest',phone='03001234567',address='123 Lahore street',zone=self.zone,note='')
        c=Client()
        response=c.post(reverse('raynoor_retail_register'),{'name':'Buyer','phone':'03001234567','password':'StrongExamplePass_624!'})
        self.assertEqual(response.status_code,302)
        self.assertTrue(OnlineRetailAccount.objects.filter(phone='03001234567').exists())
        response=c.get(reverse('raynoor_account'))
        self.assertEqual(response.status_code,200)
        self.assertNotContains(response,guest.order_number)

class RayNoorStorefrontUITests(TestCase):
    """Protect the storefront branding and navigation against template regressions."""

    def test_storefront_uses_main_raynoor_logo_and_cart_link(self):
        response = self.client.get(reverse('raynoor_store'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'images/RAYNOOR-2-LOGO.png')
        self.assertContains(response, 'RayNoor Organic Farms')
        self.assertContains(response, f'href="{reverse("raynoor_cart")}"')
        self.assertContains(response, 'Shop fresh eggs')

    def test_empty_cart_has_clear_shop_button_and_shared_logo(self):
        response = self.client.get(reverse('raynoor_cart'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Your cart is empty')
        self.assertContains(response, 'images/RAYNOOR-2-LOGO.png')
        self.assertContains(response, f'href="{reverse("raynoor_store")}"')

    def test_public_home_links_to_online_shop(self):
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{reverse("raynoor_store")}"')
        self.assertContains(response, 'Shop Eggs')

    def test_checkout_has_no_duplicate_preview_script_in_title(self):
        from api.models.egg_pos import EggPOSProduct, EggPOSInventoryLot, EggPOSSupplier
        from api.models.online_shop import OnlineOffer, OnlineDeliveryZone
        product = EggPOSProduct.objects.create(name='Retail UI test eggs', sku='SHOP-UI-TEST-012', default_sale_price=Decimal('20'))
        supplier = EggPOSSupplier.objects.create(name='UI Test Supplier')
        EggPOSInventoryLot.objects.create(product=product, supplier=supplier, source_type='purchase', lot_code='SHOP-UI-TEST', quantity_received=50, quantity_remaining=50, unit_cost=Decimal('12'))
        offer = OnlineOffer.objects.create(product=product, pack_size=12, price_per_pack=Decimal('250'), is_active=True)
        OnlineDeliveryZone.objects.create(name='UI Test Lahore Area', delivery_fee=Decimal('100'), minimum_order=Decimal('100'), is_active=True)
        self.client.post(reverse('raynoor_cart_update'), {'offer_id': offer.id, 'count': 1})
        response = self.client.get(reverse('raynoor_checkout'))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('<title>Checkout · RayNoor Organic Farms</title>', html)
        self.assertEqual(html.count("const select=document.getElementById('zone')"), 1)
