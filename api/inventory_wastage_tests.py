"""Retail inventory loss regression tests; run with python manage.py test api.inventory_wastage_tests."""
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from api.models.accounting import JournalEntry
from api.models.egg_pos import (
    EggPOSInventoryLot, EggPOSInventoryWriteOff, EggPOSProduct, EggPOSSupplier,
    EggPOSUserAccess,
)
from api.models.online_shop import OnlineDeliveryZone, OnlineOffer, OnlineOrder, OnlineOrderItem, OnlineOrderItemSource
from api.services.accounting import income_statement, trial_balance
from api.services.egg_pos_wastage import record_writeoff, void_writeoff


class RetailWastageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser('wastage_admin', 'wastage@example.test', 'strong-test-pass-1234')
        cls.employee = get_user_model().objects.create_user('wastage_seller', password='strong-test-pass-1234')
        EggPOSUserAccess.objects.create(user=cls.employee, can_sell=True, can_manage=False)
        cls.manager = get_user_model().objects.create_user('wastage_manager', password='strong-test-pass-1234')
        EggPOSUserAccess.objects.create(user=cls.manager, can_sell=True, can_manage=True)
        cls.supplier = EggPOSSupplier.objects.create(name='Wastage test supplier')
        cls.farm = EggPOSProduct.objects.create(name='Wastage Farm Eggs', sku='WAST-FARM', category='egg')
        cls.outside = EggPOSProduct.objects.create(name='Wastage Purchased Eggs', sku='WAST-PURCHASE', category='egg', selling_product=cls.farm)
        cls.ghee = EggPOSProduct.objects.create(name='Wastage Ghee 500g', sku='WAST-GHEE', category='ghee')
        cls.zone = OnlineDeliveryZone.objects.create(name='Wastage zone', delivery_fee=Decimal('0'), is_active=True)
        cls.offer = OnlineOffer.objects.create(product=cls.farm, pack_size=12, price_per_pack=Decimal('480'), is_active=True)

    def lot(self, product, qty, cost, code, expiry=None):
        return EggPOSInventoryLot.objects.create(
            product=product, source_type='purchase', supplier=self.supplier,
            lot_code=code, received_date=timezone.localdate() - timedelta(days=5),
            expiry_date=expiry, quantity_received=qty, quantity_remaining=qty,
            unit_cost=Decimal(str(cost)),
        )

    def reserve(self, qty, *, packed=False, source=None):
        order = OnlineOrder.objects.create(
            customer_name='Test reservation', customer_phone='03001234567', address='Lahore',
            zone=self.zone, status=OnlineOrder.PACKED if packed else OnlineOrder.CONFIRMED,
            subtotal=Decimal('400'), delivery_fee=Decimal('0'), total=Decimal('400'),
        )
        item = OnlineOrderItem.objects.create(
            order=order, offer=self.offer, product=self.farm, product_name=self.farm.name,
            pack_size=12, pack_count=1, egg_quantity=qty,
            pack_price=Decimal('400'), line_total=Decimal('400'),
        )
        if source:
            OnlineOrderItemSource.objects.create(item=item, source_product=source, quantity=qty)
        return order

    def record(self, lot, qty, classification='routine', reason='breakage', notes='', token=None):
        return record_writeoff(
            lot_id=lot.pk, quantity=qty, classification=classification, reason=reason,
            notes=notes, actor=self.admin, submission_token=token,
        )

    def test_damage_5_farm_5_outside_separate_sku_and_exact_cogs(self):
        farm = self.lot(self.farm, 47, 20, 'WAST-LOT-FARM')
        out = self.lot(self.outside, 60, 28, 'WAST-LOT-OUTSIDE')
        w1 = self.record(farm, 5)
        w2 = self.record(out, 5)
        self.assertEqual((w1.total_cost, w2.total_cost), (Decimal('100.00'), Decimal('140.00')))
        farm.refresh_from_db(); out.refresh_from_db()
        self.assertEqual((farm.quantity_remaining, out.quantity_remaining), (42, 55))
        for writeoff in (w1, w2):
            journal = JournalEntry.objects.get(source_key=f'egg_pos:wastage:{writeoff.id}')
            self.assertEqual(journal.lines.get(account__code='5010').debit, writeoff.total_cost)
            self.assertEqual(journal.lines.get(account__code='1200').credit, writeoff.total_cost)
            self.assertFalse(journal.lines.filter(account__code__in=['1000', '1010', '1040']).exists())
        statement = income_statement(timezone.localdate(), timezone.localdate())
        self.assertEqual(statement['cogs'], Decimal('240.00'))
        self.assertEqual(statement['gross_profit'], Decimal('-240.00'))
        _, debit, credit = trial_balance(timezone.localdate())
        self.assertEqual(debit, credit)

    def test_exceptional_ghee_jars_recorded_as_operating_expense(self):
        jar = self.lot(self.ghee, 5, 1500, 'WAST-GHEE-LOT')
        record = self.record(jar, 2, 'exceptional', 'accident', 'Two jars smashed in accident')
        self.assertEqual(record.total_cost, Decimal('3000.00'))
        jar.refresh_from_db()
        self.assertEqual(jar.quantity_remaining, 3)
        journal = JournalEntry.objects.get(source_key=f'egg_pos:wastage:{record.id}')
        self.assertEqual(journal.lines.get(account__code='6150').debit, Decimal('3000.00'))
        self.assertEqual(journal.lines.get(account__code='1200').credit, Decimal('3000.00'))
        statement = income_statement(timezone.localdate(), timezone.localdate())
        self.assertEqual(statement['cogs'], Decimal('0.00'))
        self.assertEqual(statement['expenses'], Decimal('3000.00'))
        self.assertEqual(statement['net_profit'], Decimal('-3000.00'))

    def test_online_group_reservations_cannot_be_written_off(self):
        farm = self.lot(self.farm, 7, 20, 'WAST-ONLINE-FARM')
        out = self.lot(self.outside, 5, 28, 'WAST-ONLINE-OUT')
        self.reserve(10)
        with self.assertRaisesRegex(ValueError, 'reserved for online'):
            self.record(farm, 3)
        self.assertEqual(EggPOSInventoryWriteOff.objects.count(), 0)
        farm.refresh_from_db()
        self.assertEqual(farm.quantity_remaining, 7)
        self.record(farm, 2)
        self.assertEqual(farm.writeoffs.count(), 1)

    def test_packed_source_holds_cannot_be_written_off(self):
        farm = self.lot(self.farm, 10, 20, 'WAST-PACK-FARM')
        self.lot(self.outside, 30, 28, 'WAST-PACK-OUT')
        self.reserve(8, packed=True, source=self.farm)
        with self.assertRaisesRegex(ValueError, 'allocated to packed'):
            self.record(farm, 3)
        self.assertEqual(farm.writeoffs.count(), 0)

    def test_expired_stock_written_off_without_reducing_online_sellable_stock(self):
        expired = self.lot(self.farm, 5, 20, 'WAST-EXPIRED', expiry=timezone.localdate()-timedelta(days=1))
        self.lot(self.outside, 12, 28, 'WAST-EXPIRED-OTHER')
        self.reserve(12)
        record = self.record(expired, 5, reason='expired')
        self.assertEqual(record.total_cost, Decimal('100.00'))
        expired.refresh_from_db()
        self.assertEqual(expired.quantity_remaining, 0)

    def test_quantity_invalid_exceptional_reason_invalid_and_duplicate_token(self):
        lot = self.lot(self.ghee, 5, 1500, 'WAST-VALIDATION')
        for quantity in [0, -1, 6, 'abc']:
            with self.assertRaises(ValueError):
                self.record(lot, quantity)
        with self.assertRaisesRegex(ValueError, 'Explain'):
            self.record(lot, 1, classification='exceptional')
        with self.assertRaisesRegex(ValueError, 'not expired'):
            self.record(lot, 1, reason='expired')
        token = uuid4()
        self.record(lot, 1, token=token)
        with self.assertRaisesRegex(ValueError, 'already been submitted'):
            self.record(lot, 1, token=token)
        lot.refresh_from_db()
        self.assertEqual(lot.quantity_remaining, 4)
        self.assertEqual(lot.writeoffs.count(), 1)

    def test_admin_reversal_restores_inventory_with_offset_journal_audit(self):
        lot = self.lot(self.farm, 20, 20, 'WAST-REVERSE')
        record = self.record(lot, 4)
        with self.assertRaisesRegex(ValueError, 'Only an administrator'):
            void_writeoff(writeoff_id=record.pk, actor=self.manager, reason='Corrected count was wrong')
        record = void_writeoff(writeoff_id=record.pk, actor=self.admin, reason='Corrected count was wrong')
        self.assertTrue(record.is_voided)
        self.assertEqual(record.void_reason, 'Corrected count was wrong')
        lot.refresh_from_db()
        self.assertEqual(lot.quantity_remaining, 20)
        self.assertTrue(JournalEntry.objects.filter(source_key=f'egg_pos:wastage:{record.pk}').exists())
        self.assertTrue(JournalEntry.objects.filter(source_key=f'egg_pos:wastage:reverse:{record.pk}').exists())
        statement = income_statement(timezone.localdate(), timezone.localdate())
        self.assertEqual(statement['cogs'], Decimal('0.00'))
        with self.assertRaisesRegex(ValueError, 'already been reversed'):
            void_writeoff(writeoff_id=record.pk, actor=self.admin, reason='Attempting second reversal')

    def test_management_page_and_post_with_seller_forbidden(self):
        lot = self.lot(self.outside, 60, 28, 'WAST-WEB-LOT')
        client = Client()
        client.force_login(self.employee)
        self.assertEqual(client.get(reverse('egg_pos_wastage')).status_code, 403)
        client.force_login(self.manager)
        self.assertEqual(client.get(reverse('egg_pos_wastage')).status_code, 200)
        token = str(uuid4())
        response = client.post(reverse('egg_pos_wastage'), {
            'lot_id': lot.pk, 'quantity': '3', 'classification': 'routine',
            'reason': 'breakage', 'notes': 'Dropped during loading', 'submission_token': token,
        })
        self.assertEqual(response.status_code, 302)
        lot.refresh_from_db()
        self.assertEqual(lot.quantity_remaining, 57)
        self.assertEqual(EggPOSInventoryWriteOff.objects.count(), 1)
        self.assertEqual(client.post(reverse('egg_pos_void_wastage', args=[EggPOSInventoryWriteOff.objects.first().pk]), {
            'void_reason': 'Entered wrong count', 'admin_password': 'strong-test-pass-1234'
        }).status_code, 403)
        client.force_login(self.admin)
        response = client.post(reverse('egg_pos_void_wastage', args=[EggPOSInventoryWriteOff.objects.first().pk]), {
            'void_reason': 'Entered wrong count', 'admin_password': 'strong-test-pass-1234',
        })
        self.assertEqual(response.status_code, 302)
        lot.refresh_from_db()
        self.assertEqual(lot.quantity_remaining, 60)
        self.assertTrue(EggPOSInventoryWriteOff.objects.first().is_voided)

    def test_supplier_purchase_cost_correction_updates_wastage_journals(self):
        from api.services.egg_pos_wastage import revalue_writeoffs_for_lot
        lot = self.lot(self.outside, 10, 28, 'WAST-COST-CORRECT')
        record = self.record(lot, 3)
        self.assertEqual(record.total_cost, Decimal('84.00'))
        count = revalue_writeoffs_for_lot(lot=lot, new_unit_cost=Decimal('30.000000'), actor=self.admin)
        self.assertEqual(count, 1)
        record.refresh_from_db()
        self.assertEqual(record.total_cost, Decimal('90.00'))
        self.assertIn('Supplier cost correction', record.notes)
        self.assertEqual(JournalEntry.objects.get(
            source_key=f'egg_pos:wastage:{record.id}',
        ).lines.get(account__code='5010').debit, Decimal('90.00'))
        void_writeoff(writeoff_id=record.pk, actor=self.admin, reason='Wrong count due to double entry')
        count = revalue_writeoffs_for_lot(lot=lot, new_unit_cost=Decimal('31.000000'), actor=self.admin)
        self.assertEqual(count, 1)
        record.refresh_from_db()
        self.assertEqual(record.total_cost, Decimal('93.00'))
        self.assertEqual(JournalEntry.objects.get(
            source_key=f'egg_pos:wastage:reverse:{record.id}',
        ).lines.get(account__code='5010').credit, Decimal('93.00'))
        statement = income_statement(timezone.localdate(), timezone.localdate())
        self.assertEqual(statement['cogs'], Decimal('0.00'))
