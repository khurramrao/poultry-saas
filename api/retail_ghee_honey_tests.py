"""Integration tests for finished-jar stock, sticker cost, mixed invoices and online reservations."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from api.models.accounting import JournalEntry
from api.models.egg_pos import (
    EggPOSProduct, EggPOSSupplier, EggPOSPurchase, EggPOSInventoryLot,
    EggPOSSale, EggPOSSaleItem,
)
from api.models.online_shop import OnlineOffer, OnlineDeliveryZone
from api.services.online_shop import checkout_order, action_order
from api.services.egg_pos import available_product_stock


class RetailGheeHoneyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(
            username='retail_product_owner', email='retail@example.test', password='safe-password-1234')
        cls.supplier = EggPOSSupplier.objects.create(name='Sealed jar wholesaler')
        cls.ghee = EggPOSProduct.objects.create(
            name='RayNoor Cow Ghee 500g', sku='GHEE-COW-500-TEST', category='ghee',
            weight_grams=Decimal('500'), default_sale_price=Decimal('3500'), low_stock_eggs=5)
        cls.honey = EggPOSProduct.objects.create(
            name='RayNoor Honey 1kg', sku='HONEY-1000-TEST', category='honey',
            weight_grams=Decimal('1000'), default_sale_price=Decimal('4500'), low_stock_eggs=3)
        cls.eggs = EggPOSProduct.objects.create(
            name='Test eggs 50g', sku='EGG-50-RETAIL-TEST', default_sale_price=Decimal('30'))
        cls.zone = OnlineDeliveryZone.objects.create(
            name='Retail test zone', delivery_fee=Decimal('150'), is_active=True)

    def client_as_owner(self):
        client = Client()
        client.force_login(self.admin)
        return client

    def make_lot(self, product, qty, unit_cost, code):
        return EggPOSInventoryLot.objects.create(
            product=product, source_type='purchase', supplier=self.supplier,
            lot_code=code, received_date=timezone.localdate(),
            quantity_received=qty, quantity_remaining=qty,
            unit_cost=Decimal(str(unit_cost)),
        )

    def test_sealed_jars_purchased_with_sticker_cost_are_capitalized_not_supplier_payable(self):
        client = self.client_as_owner()
        response = client.post(reverse('egg_pos_add_purchase'), {
            'supplier': self.supplier.pk, 'purchase_date': str(timezone.localdate()),
            'reference': 'VENDOR-GHEE-001', 'transport_cost': '40.00',
            'transport_payment_method': 'cash', 'product_id': [self.ghee.pk],
            'quantity': ['4'], 'line_total_cost': ['10000.00'],
            'sticker_cost_total': ['80.00'], 'expiry_date': [''],
        })
        self.assertEqual(response.status_code, 302)
        purchase = EggPOSPurchase.objects.get()
        self.assertEqual(purchase.total_amount, Decimal('10000'))
        self.assertEqual(purchase.sticker_total, Decimal('80'))
        self.assertEqual(purchase.landed_total, Decimal('10120'))
        self.assertEqual(purchase.balance_due, Decimal('10000'))
        lot = purchase.items.get().lot
        self.assertEqual(lot.quantity_remaining, 4)
        self.assertEqual(lot.unit_cost, Decimal('2530.000000'))
        journal = JournalEntry.objects.get(source_key=f'egg_pos:purchase:{purchase.pk}')
        self.assertEqual(sum(row.debit for row in journal.lines.all()), Decimal('10120'))
        self.assertEqual(sum(row.credit for row in journal.lines.all()), Decimal('10120'))
        self.assertEqual(sum(row.credit for row in journal.lines.filter(account__code='1000')), Decimal('120'))

    def test_egg_and_ghee_same_invoice_with_correct_units_and_cogs(self):
        client = self.client_as_owner()
        egg_lot = self.make_lot(self.eggs, 12, 20, 'RT-EGG-LOT')
        jar_lot = self.make_lot(self.ghee, 10, 2500, 'RT-GHEE-LOT')
        response = client.post(reverse('egg_pos_new_sale'), {
            'sale_date': str(timezone.localdate()),
            'product_id': [self.eggs.pk, self.ghee.pk],
            'sale_unit': ['dozen', 'jar'],
            'unit_count': ['1', '2'],
            'unit_rate': ['500', '3300'],
            'discount_amount': '0', 'payment_type': 'full', 'payment_method': 'cash',
            'customer_name': 'Retail Sale Test',
        })
        self.assertEqual(response.status_code, 302)
        sale = EggPOSSale.objects.get()
        self.assertEqual(sale.subtotal, Decimal('7100'))
        self.assertEqual(sale.cogs_total, Decimal('5240'))
        self.assertEqual(sale.profit_total, Decimal('1860'))
        jar_item = sale.items.get(product=self.ghee)
        self.assertEqual(jar_item.sale_unit, 'jar')
        self.assertEqual(jar_item.quantity, 2)
        self.assertIn('2 jars', jar_item.display_sale_quantity)
        egg_lot.refresh_from_db(); jar_lot.refresh_from_db()
        self.assertEqual(egg_lot.quantity_remaining, 0)
        self.assertEqual(jar_lot.quantity_remaining, 8)

    def test_online_jar_reserves_one_each_and_delivers_once(self):
        jar_lot = self.make_lot(self.honey, 4, 3000, 'RT-HONEY-LOT')
        offer = OnlineOffer.objects.create(product=self.honey, pack_size=1,
            price_per_pack=Decimal('4500'), is_active=True)
        order = checkout_order({str(offer.pk): 2}, account=None, name='Jar Buyer',
            phone='03001234567', address='Test home Lahore', zone=self.zone, note='')
        self.assertEqual(order.items.get().egg_quantity, 2)  # internal legacy field counts physical units
        self.assertEqual(available_product_stock(self.honey), 2)
        action_order(order.pk, self.admin, 'confirm')
        action_order(order.pk, self.admin, 'pack')
        action_order(order.pk, self.admin, 'dispatch', delivery_method='own', rider_name='Test courier')
        order = action_order(order.pk, self.admin, 'deliver')
        self.assertEqual(order.pos_sale.cogs_total, Decimal('6000'))
        self.assertEqual(order.pos_sale.items.get().sale_unit, 'jar')
        jar_lot.refresh_from_db()
        self.assertEqual(jar_lot.quantity_remaining, 2)
        self.assertEqual(action_order(order.pk, self.admin, 'deliver').pos_sale_id, order.pos_sale_id)
        self.assertEqual(EggPOSSale.objects.count(), 1)

    def test_online_mixed_cart_of_eggs_and_honey_jars(self):
        self.make_lot(self.eggs, 30, 18, 'RT-COMBO-EGG')
        self.make_lot(self.honey, 5, 3000, 'RT-COMBO-HONEY')
        egg_offer = OnlineOffer.objects.create(product=self.eggs, pack_size=12,
            price_per_pack=Decimal('450'), is_active=True)
        honey_offer = OnlineOffer.objects.create(product=self.honey, pack_size=1,
            price_per_pack=Decimal('4500'), is_active=True)
        order = checkout_order({str(egg_offer.pk): 1, str(honey_offer.pk): 2},
            account=None, name='Combo buyer', phone='03001234567',
            address='Lahore delivery', zone=self.zone, note='')
        self.assertEqual(order.subtotal, Decimal('9450'))
        self.assertEqual(available_product_stock(self.eggs), 18)
        self.assertEqual(available_product_stock(self.honey), 3)
        action_order(order.pk, self.admin, 'confirm')
        action_order(order.pk, self.admin, 'pack')
        action_order(order.pk, self.admin, 'dispatch', delivery_method='own', rider_name='Test courier')
        order = action_order(order.pk, self.admin, 'deliver')
        items = {item.product_id: item for item in order.pos_sale.items.all()}
        self.assertEqual(items[self.eggs.pk].quantity, 12)
        self.assertEqual(items[self.honey.pk].quantity, 2)
        self.assertEqual(items[self.honey.pk].sale_unit, 'jar')
        self.assertEqual(order.pos_sale.cogs_total, Decimal('6216'))
        self.assertEqual(EggPOSSale.objects.count(), 1)

    def test_reject_egg_as_jar_and_ghee_as_dozen(self):
        self.make_lot(self.eggs, 30, 10, 'RT-INVALID-EGG')
        self.make_lot(self.ghee, 30, 100, 'RT-INVALID-GHEE')
        client = self.client_as_owner()
        for product, unit in [(self.eggs, 'jar'), (self.ghee, 'dozen')]:
            response = client.post(reverse('egg_pos_new_sale'), {
                'sale_date': str(timezone.localdate()), 'product_id': [product.pk],
                'sale_unit': [unit], 'unit_count': ['1'], 'unit_rate': ['200'],
                'payment_type': 'full', 'payment_method': 'cash',
            })
            self.assertEqual(response.status_code, 200)
        self.assertEqual(EggPOSSale.objects.count(), 0)

    def test_online_offer_form_enforces_jar_pack_of_one(self):
        from api.forms_online_shop import OnlineOfferSetupForm
        form = OnlineOfferSetupForm(data={
            'product': self.ghee.pk, 'pack_size': 12, 'title': 'Invalid Ghee Dozen',
            'price_per_pack': '3500.00', 'is_active': 'on',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('pack_size', form.errors)
