"""Read-only SKU report regression coverage: physical SKU, holds, profit, access."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from api.models.egg_pos import (
    EggPOSProduct, EggPOSSupplier, EggPOSInventoryLot, EggPOSSale,
    EggPOSSaleItem, EggPOSSaleAllocation, EggPOSUserAccess,
)
from api.models.egg_pos_prebooking import EggPOSManualPrebooking, EggPOSManualPrebookingLine
from api.models.online_shop import (
    OnlineDeliveryZone, OnlineOffer, OnlineOrder, OnlineOrderItem, OnlineOrderItemSource,
)
from api.services.egg_pos_sku_report import make_report


class SKUSalesBookingReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.admin = get_user_model().objects.create_superuser('skuadmin', 'sku@example.test', 'safe-test-password')
        cls.seller = get_user_model().objects.create_user('skuseller', password='safe-test-password')
        EggPOSUserAccess.objects.create(user=cls.seller, can_sell=True, can_manage=False)
        cls.supplier = EggPOSSupplier.objects.create(name='SKU Report Test Supplier')
        cls.farm = EggPOSProduct.objects.create(name='Farm eggs 45g test', sku='SKUR-FARM', category='egg', low_stock_eggs=4)
        cls.outside = EggPOSProduct.objects.create(name='Outside eggs 45g test', sku='SKUR-OUTSIDE', category='egg', selling_product=cls.farm, low_stock_eggs=4)
        cls.ghee = EggPOSProduct.objects.create(name='Ghee 500g report test', sku='SKUR-GHEE-500', category='ghee', weight_grams=500)
        cls.zone = OnlineDeliveryZone.objects.create(name='SKU report delivery zone', delivery_fee=100, is_active=True)
        cls.farm_lot = cls.lot(cls.farm, 41, 20, 'SKUR-FARM-LOT')
        cls.outside_lot = cls.lot(cls.outside, 54, 28, 'SKUR-OUT-LOT')
        cls.ghee_lot = cls.lot(cls.ghee, 9, 1200, 'SKUR-GHEE-LOT')
        cls.offer_eggs = OnlineOffer.objects.create(product=cls.farm, pack_size=12, price_per_pack=400)
        cls.offer_ghee = OnlineOffer.objects.create(product=cls.ghee, pack_size=1, price_per_pack=1600)

    @classmethod
    def lot(cls, product, remaining, cost, code):
        return EggPOSInventoryLot.objects.create(
            product=product, source_type='purchase', supplier=cls.supplier,
            lot_code=code, received_date=cls.today,
            quantity_received=remaining, quantity_remaining=remaining,
            unit_cost=Decimal(cost),
        )

    def sale_egg_mix(self, farm, outside, *, sale_date=None, discount='0', reversed=False):
        qty = farm + outside
        gross = Decimal(qty * 30)
        sale = EggPOSSale.objects.create(
            sale_date=sale_date or self.today, created_by=self.admin,
            subtotal=gross, discount_amount=Decimal(discount),
            net_total=gross - Decimal(discount), cogs_total=farm * 20 + outside * 28,
            profit_total=gross - Decimal(discount) - (farm * 20 + outside * 28),
            is_reversed=reversed,
        )
        line = EggPOSSaleItem.objects.create(
            sale=sale, product=self.farm, quantity=qty,
            unit_price=Decimal('30'), unit_rate=gross, line_total=gross,
            cogs_amount=Decimal(farm * 20 + outside * 28),
        )
        if farm:
            EggPOSSaleAllocation.objects.create(sale_item=line, lot=self.farm_lot,
                quantity=farm, unit_cost=Decimal('20'), cogs_amount=Decimal(farm * 20))
        if outside:
            EggPOSSaleAllocation.objects.create(sale_item=line, lot=self.outside_lot,
                quantity=outside, unit_cost=Decimal('28'), cogs_amount=Decimal(outside * 28))
        return sale

    def order(self, product, offer, quantity, status, *, future=True):
        order = OnlineOrder.objects.create(
            customer_name='Online report test', customer_phone='03001234567',
            address='Lahore test address', zone=self.zone, status=status,
            subtotal=Decimal('400'), delivery_fee=Decimal('100'), total=Decimal('500'),
            reserved_until=timezone.now() + (timedelta(minutes=20) if future else -timedelta(minutes=20)),
        )
        item = OnlineOrderItem.objects.create(
            order=order, offer=offer, product=product, product_name=product.name,
            pack_size=12 if product.is_egg else 1, pack_count=1,
            egg_quantity=quantity, pack_price=Decimal('400'), line_total=Decimal('400'),
        )
        return order, item

    def test_mix_6_and_6_tracks_source_and_cost_and_discount(self):
        self.sale_egg_mix(6, 6, discount='24')
        report = make_report(self.today, self.today)
        rows = {row['product'].sku: row for row in report['rows']}
        self.assertEqual(rows['SKUR-FARM']['sold'], 6)
        self.assertEqual(rows['SKUR-OUTSIDE']['sold'], 6)
        self.assertEqual(rows['SKUR-FARM']['cogs'], Decimal('120'))
        self.assertEqual(rows['SKUR-OUTSIDE']['cogs'], Decimal('168'))
        self.assertEqual(rows['SKUR-FARM']['revenue'], Decimal('168'))
        self.assertEqual(rows['SKUR-OUTSIDE']['revenue'], Decimal('168'))
        self.assertEqual(report['profit'], Decimal('48'))

    def test_mix_10_and_2_and_reversed_not_counted(self):
        self.sale_egg_mix(10, 2)
        self.sale_egg_mix(6, 6, reversed=True)
        report = make_report(self.today, self.today)
        rows = {row['product'].sku: row for row in report['rows']}
        self.assertEqual(rows['SKUR-FARM']['sold'], 10)
        self.assertEqual(rows['SKUR-OUTSIDE']['sold'], 2)
        self.assertEqual(report['cogs'], Decimal('256'))
        self.assertEqual(report['profit'], Decimal('104'))

    def test_packed_source_reservations_vs_unallocated_group_reservations(self):
        # Unpacked web order reserves group stock but cannot name source SKU.
        self.order(self.farm, self.offer_eggs, 12, OnlineOrder.CONFIRMED)
        _, packed = self.order(self.farm, self.offer_eggs, 12, OnlineOrder.PACKED)
        OnlineOrderItemSource.objects.create(item=packed, source_product=self.farm, quantity=10)
        OnlineOrderItemSource.objects.create(item=packed, source_product=self.outside, quantity=2)
        # Expired 20-minute unconfirmed checkout hold must be ignored.
        self.order(self.farm, self.offer_eggs, 12, OnlineOrder.RESERVED, future=False)
        report = make_report(self.today, self.today)
        group = next(g for g in report['groups'] if g['primary'].pk == self.farm.pk)
        rows = {r['product'].sku: r for r in report['rows']}
        self.assertEqual(group['on_hand'], 95)
        self.assertEqual(group['online_booked'], 24)
        self.assertEqual(group['fixed_booked'], 12)
        self.assertEqual(group['unallocated_booked'], 12)
        self.assertEqual(group['available'], 71)
        self.assertEqual(rows['SKUR-FARM']['online_booked'], 10)
        self.assertEqual(rows['SKUR-OUTSIDE']['online_booked'], 2)
        self.assertEqual(rows['SKUR-FARM']['available'], 31)
        self.assertEqual(rows['SKUR-OUTSIDE']['available'], 52)

    def test_manual_prebooking_is_not_stock_reservation(self):
        pre = EggPOSManualPrebooking.objects.create(
            customer_name='Prebook customer', customer_phone='03009876543',
            channel='whatsapp', status='confirmed', created_by=self.admin,
        )
        EggPOSManualPrebookingLine.objects.create(
            booking=pre, product=self.farm, quantity=2, unit='dozen',
        )
        group = next(g for g in make_report(self.today, self.today)['groups'] if g['primary'].pk == self.farm.pk)
        self.assertEqual(group['manual_requests'], 24)
        self.assertEqual(group['available'], 95)

    def test_jar_unit_and_live_reservation(self):
        self.order(self.ghee, self.offer_ghee, 1, OnlineOrder.CONFIRMED)
        report = make_report(self.today, self.today)
        jar = next(r for r in report['rows'] if r['product'].pk == self.ghee.pk)
        self.assertEqual(jar['on_hand'], 9)
        self.assertEqual(jar['online_booked'], 1)
        self.assertEqual(jar['available'], 8)
        self.assertEqual(report['jars_booked'], 1)
        self.assertEqual(report['eggs_booked'], 0)

    def test_date_and_category_filters_only_change_sales(self):
        self.sale_egg_mix(6, 6, sale_date=self.today - timedelta(days=5))
        today_report = make_report(self.today, self.today, category='egg')
        self.assertEqual(today_report['eggs_sold'], 0)
        self.assertEqual(today_report['groups'][0]['on_hand'], 95)
        five_days = make_report(self.today - timedelta(days=5), self.today, category='egg')
        self.assertEqual(five_days['eggs_sold'], 12)
        ghee = make_report(self.today - timedelta(days=5), self.today, category='ghee')
        self.assertEqual(ghee['eggs_sold'], 0)
        self.assertEqual(ghee['group_count'], 1)

    def test_report_manager_only(self):
        url = reverse('egg_pos_sku_report')
        seller_client = Client(); seller_client.force_login(self.seller)
        self.assertEqual(seller_client.get(url).status_code, 302)
        owner_client = Client(); owner_client.force_login(self.admin)
        response = owner_client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'SKU-wise Sales &amp; Bookings')
        self.assertContains(response, 'SKUR-GHEE-500')
        self.assertContains(response, 'SKUR-FARM')
        self.assertContains(response, 'SKU-by-SKU breakdown')

    def test_invalid_dates_use_safe_default(self):
        client = Client(); client.force_login(self.admin)
        resp = client.get(reverse('egg_pos_sku_report'), {'from': 'not-a-date', 'to': '9999-99-99'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Enter valid dates')
