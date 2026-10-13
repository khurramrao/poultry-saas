"""Invoice cross-reference and online order overview (no new accounting postings)."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from api.models.egg_pos import EggPOSProduct, EggPOSInventoryLot, EggPOSSupplier, EggPOSSale, EggPOSUserAccess
from api.models.online_shop import OnlineOffer, OnlineDeliveryZone, OnlineOrder
from api.services.online_shop import checkout_order, action_order


class OnlineSalesReferenceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_superuser(
            'invoice_reference_owner', 'owner@example.test', 'safe-password-2026!'
        )
        product = EggPOSProduct.objects.create(
            name='White Eggs 605', sku='INV-605', default_sale_price=Decimal('25')
        )
        supplier = EggPOSSupplier.objects.create(name='Test Supplier')
        EggPOSInventoryLot.objects.create(
            product=product, supplier=supplier, source_type='purchase',
            lot_code='INV-605-LOT', quantity_received=300,
            quantity_remaining=300, unit_cost=Decimal('14')
        )
        cls.offer = OnlineOffer.objects.create(
            product=product, pack_size=12, price_per_pack=Decimal('360'), is_active=True
        )
        cls.zone = OnlineDeliveryZone.objects.create(
            name='Reference Lahore Zone', delivery_fee=Decimal('100'),
            minimum_order=Decimal('100'), is_active=True
        )

    def create_order(self):
        return checkout_order(
            {str(self.offer.pk): 1}, account=None, name='Retail Customer',
            phone='03001234567', address='Lahore test delivery address',
            zone=self.zone, note='',
        )

    def test_order_count_includes_placed_orders_even_before_invoice(self):
        order = self.create_order()
        self.client.force_login(self.owner)
        page = self.client.get(reverse('egg_pos_sales'))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context['online_orders_placed'], 1)
        self.assertEqual(page.context['online_orders_active'], 1)
        self.assertEqual(page.context['online_orders_invoiced'], 0)
        self.assertNotContains(page, order.order_number)
        self.assertEqual(EggPOSSale.objects.count(), 0)

    def test_delivered_invoice_displays_online_reference_and_supports_filter(self):
        online = self.create_order()
        action_order(online.pk, self.owner, 'confirm')
        action_order(online.pk, self.owner, 'pack')
        action_order(online.pk, self.owner, 'dispatch', delivery_method='own', rider_name='Rider')
        online = action_order(online.pk, self.owner, 'deliver')
        sale = EggPOSSale.objects.get(pk=online.pos_sale_id)
        manual = EggPOSSale.objects.create(
            created_by=self.owner, sale_number='RNE-COUNTER-TEST', customer_name='Counter',
            subtotal=Decimal('25'), net_total=Decimal('25'),
        )
        self.client.force_login(self.owner)
        page = self.client.get(reverse('egg_pos_sales'))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, online.order_number)
        self.assertContains(page, sale.sale_number)
        self.assertContains(page, reverse('raynoor_admin_order_detail', kwargs={'order_id': online.pk}))
        self.assertEqual(page.context['online_orders_placed'], 1)
        self.assertEqual(page.context['online_orders_active'], 0)
        self.assertEqual(page.context['online_orders_invoiced'], 1)
        online_only = self.client.get(reverse('egg_pos_sales'), {'source': 'online'})
        self.assertEqual(online_only.status_code, 200)
        self.assertEqual([s.id for s in online_only.context['sales']], [sale.id])
        self.assertNotContains(online_only, manual.sale_number)
        counter_only = self.client.get(reverse('egg_pos_sales'), {'source': 'counter'})
        self.assertEqual([s.id for s in counter_only.context['sales']], [manual.id])
        self.assertNotContains(counter_only, online.order_number)
        self.assertContains(self.client.get(reverse('egg_pos_sale_invoice', kwargs={'sale_id': sale.id})), online.order_number)
        self.assertEqual(EggPOSSale.objects.count(), 2)

    def test_salesperson_does_not_see_all_online_orders_or_admin_links(self):
        """Maintain existing staff-visible-sales permission boundaries."""
        self.create_order()
        employee = get_user_model().objects.create_user(
            'invoice_reference_staff', password='some-strong-password'
        )
        EggPOSUserAccess.objects.create(user=employee, can_sell=True, can_manage=False)
        self.client.force_login(employee)
        page = self.client.get(reverse('egg_pos_sales'))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'Online orders placed (all time)')
        self.assertNotContains(page, 'All Online Orders')
        self.assertNotContains(page, 'RNWEB-000001')

    def test_invalid_filter_is_safe_and_cancellation_is_not_invoiced(self):
        order = self.create_order()
        action_order(order.pk, self.owner, 'cancel', reason='Cancelled by customer')
        self.client.force_login(self.owner)
        page = self.client.get(reverse('egg_pos_sales'), {'source': 'garbage'})
        self.assertEqual(page.context['source'], 'all')
        self.assertEqual(page.context['online_orders_placed'], 1)
        self.assertEqual(page.context['online_orders_active'], 0)
        self.assertEqual(page.context['online_orders_invoiced'], 0)
