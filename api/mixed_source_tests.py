"""Focused tests: run `python manage.py test api.mixed_source_tests` in the real environment."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from api.models.egg_pos import (
    EggPOSProduct, EggPOSSupplier, EggPOSInventoryLot, EggPOSSale, EggPOSSaleItem,
    EggPOSSaleAllocation,
)
from api.models.online_shop import OnlineOffer, OnlineDeliveryZone, OnlineOrder, OnlineOrderItemSource
from api.services.egg_pos import available_product_stock
from api.services.egg_pos_mixed_sources import allocate_selected_sources, packed_source_reserved
from api.services.online_shop import checkout_order, action_order, free_available


class MixedSourceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_superuser(
            username='mixed_source_test_owner', email='owner@example.test', password='test-password',
        )
        cls.primary = EggPOSProduct.objects.create(
            name='Organic farm 45g selling SKU', sku='MIX-FARM-45', default_sale_price=Decimal('30'),
        )
        cls.outside = EggPOSProduct.objects.create(
            name='Organic outside 45g source SKU', sku='MIX-OUT-45', selling_product=cls.primary,
            default_sale_price=Decimal('30'),
        )
        cls.supplier = EggPOSSupplier.objects.create(name='Mixed source test supplier')
        cls.zone = OnlineDeliveryZone.objects.create(
            name='Mixed Test Zone', delivery_fee=Decimal('100'), is_active=True,
        )
        cls.offer = OnlineOffer.objects.create(
            product=cls.primary, pack_size=12, price_per_pack=Decimal('1200'), is_active=True,
        )

    def lot(self, source, eggs, cost, code):
        return EggPOSInventoryLot.objects.create(
            product=source, source_type='purchase', supplier=self.supplier, lot_code=code,
            received_date=timezone.localdate(), quantity_received=eggs,
            quantity_remaining=eggs, unit_cost=Decimal(str(cost)),
        )

    def order(self):
        return checkout_order(
            {str(self.offer.pk): 1}, account=None, name='Buyer',
            phone='03001234567', address='Test delivery address Lahore',
            zone=self.zone, note='',
        )

    def pos_sale_item(self, qty):
        sale = EggPOSSale.objects.create(
            sale_date=timezone.localdate(), customer_name='Walk-in',
            created_by=self.owner, subtotal=Decimal('500'), net_total=Decimal('500'),
        )
        return EggPOSSaleItem.objects.create(
            sale=sale, product=self.primary, quantity=qty,
            unit_count=qty, unit_size=1, unit_rate=Decimal('30'),
            unit_price=Decimal('30'), line_total=Decimal('360'),
        )

    def test_two_different_carton_mixes_use_exact_cost_and_balances(self):
        farm = self.lot(self.primary, 47, 20, 'MIX-FARM-LOT')
        outside = self.lot(self.outside, 60, 28, 'MIX-OUT-LOT')
        self.assertEqual(available_product_stock(self.primary), 107)
        sale1 = self.pos_sale_item(12)
        self.assertEqual(allocate_selected_sources(sale1, timezone.localdate(), {
            self.primary.pk: 6, self.outside.pk: 6,
        }), Decimal('288.00'))
        farm.refresh_from_db(); outside.refresh_from_db()
        self.assertEqual((farm.quantity_remaining, outside.quantity_remaining), (41, 54))
        sale2 = self.pos_sale_item(12)
        self.assertEqual(allocate_selected_sources(sale2, timezone.localdate(), {
            self.primary.pk: 10, self.outside.pk: 2,
        }), Decimal('256.00'))
        farm.refresh_from_db(); outside.refresh_from_db()
        self.assertEqual((farm.quantity_remaining, outside.quantity_remaining), (31, 52))
        self.assertEqual(free_available(self.primary.pk), 83)
        self.assertEqual(sum(a.quantity for a in EggPOSSaleAllocation.objects.all()),24)

    def test_online_checkout_reserves_total_then_repack_with_new_farm_arrival(self):
        outside = self.lot(self.outside, 12, 28, 'MIX-ONLY-OUTSIDE')
        order = self.order()
        self.assertEqual(free_available(self.primary.pk), 0)
        action_order(order.pk, self.owner, 'confirm')
        item = order.items.first()
        action_order(order.pk, self.owner, 'pack', source_allocations={item.pk:{
            self.primary.pk:0, self.outside.pk:12,
        }})
        self.assertEqual(packed_source_reserved(self.outside.pk),12)
        farm = self.lot(self.primary, 6, 20, 'MIX-ARRIVED-FARM')
        action_order(order.pk, self.owner, 'repack', source_allocations={item.pk:{
            self.primary.pk:6, self.outside.pk:6,
        }})
        self.assertEqual(packed_source_reserved(self.outside.pk),6)
        action_order(order.pk, self.owner, 'dispatch', delivery_method='own', rider_name='Rider')
        delivered = action_order(order.pk, self.owner, 'deliver')
        self.assertEqual(delivered.pos_sale.cogs_total, Decimal('288.00'))
        farm.refresh_from_db(); outside.refresh_from_db()
        self.assertEqual((farm.quantity_remaining, outside.quantity_remaining), (0,6))
        self.assertEqual(OnlineOrderItemSource.objects.filter(item=item).count(), 2)
        self.assertEqual(action_order(order.pk, self.owner, 'deliver').pos_sale_id, delivered.pos_sale_id)
        self.assertEqual(EggPOSSale.objects.filter(pk=delivered.pos_sale_id).count(),1)

    def test_packed_source_is_locked_for_other_pos_sales(self):
        self.lot(self.primary, 12, 20, 'MIX-POS-FARM')
        self.lot(self.outside, 12, 28, 'MIX-POS-OUT')
        order = self.order(); action_order(order.pk, self.owner, 'confirm')
        item = order.items.first()
        action_order(order.pk, self.owner, 'pack', source_allocations={item.pk:{self.outside.pk:12}})
        sale_item = self.pos_sale_item(12)
        with self.assertRaises(ValueError):
            allocate_selected_sources(sale_item, timezone.localdate(), {self.outside.pk:12})
        self.assertEqual(allocate_selected_sources(sale_item, timezone.localdate(), {self.primary.pk:12}), Decimal('240.00'))
        self.assertEqual(free_available(self.primary.pk),0)

    def test_cancelling_a_packed_order_releases_both_holds(self):
        self.lot(self.outside, 12, 28, 'MIX-CANCEL-OUT')
        order = self.order(); action_order(order.pk, self.owner, 'confirm')
        item = order.items.first()
        action_order(order.pk, self.owner, 'pack', source_allocations={item.pk:{self.outside.pk:12}})
        action_order(order.pk, self.owner, 'cancel', reason='Customer cancelled')
        self.assertEqual(free_available(self.primary.pk),12)
        self.assertEqual(packed_source_reserved(self.outside.pk),0)

    def test_pos_sale_form_posts_physical_source_quantities(self):
        """The existing POS endpoint, not just the allocator, records 6+6 correctly."""
        import json
        from django.test import RequestFactory
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.contrib.sessions.middleware import SessionMiddleware
        from django.test.utils import override_settings
        from api.views.egg_pos import new_sale

        farm = self.lot(self.primary, 47, 20, 'MIX-POS-FORM-FARM')
        outside = self.lot(self.outside, 60, 28, 'MIX-POS-FORM-OUT')
        request = RequestFactory().post('/api/egg-pos/sale/new/', {
            'sale_date': str(timezone.localdate()),
            'product_id': [str(self.primary.pk)],
            'sale_unit': ['dozen'],
            'unit_count': ['1'],
            'unit_rate': ['360.00'],
            'source_allocations': [json.dumps({str(self.primary.pk):6, str(self.outside.pk):6})],
            'payment_type': 'full', 'payment_method': 'cash', 'discount_amount': '0',
        })
        request.user = self.owner
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        request._messages = FallbackStorage(request)
        response = new_sale(request)
        self.assertEqual(response.status_code, 302)
        sale = EggPOSSale.objects.order_by('-pk').first()
        self.assertEqual(sale.cogs_total, Decimal('288.00'))
        self.assertEqual(sale.profit_total, Decimal('72.00'))
        farm.refresh_from_db(); outside.refresh_from_db()
        self.assertEqual((farm.quantity_remaining, outside.quantity_remaining), (41,54))
