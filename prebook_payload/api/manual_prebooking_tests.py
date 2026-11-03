"""Run: python manage.py test api.manual_prebooking_tests"""
import uuid
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from api.models.egg_pos import EggPOSProduct, EggPOSSale, EggPOSInventoryLot
from api.models.egg_pos_prebooking import EggPOSManualPrebooking, EggPOSManualPrebookingEvent


class ManualPrebookingTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser('prebook_admin', 'prebook@example.com', 'SecureTest123!')
        self.visitor = User.objects.create_user('prebook_visitor', 'visitor@example.com', 'x')
        self.product = EggPOSProduct.objects.create(name='Prebook White Eggs', sku='PREB-EGG-01')
        self.url = reverse('egg_pos_manual_prebookings')
        self.token = str(uuid.uuid4())

    def payload(self, **kwargs):
        data = {
            'action': 'create', 'request_token': self.token,
            'customer_name': 'Customer TikTok', 'customer_phone': '03001234567',
            'channel': 'tiktok', 'delivery_area': 'Lahore',
            'product_1': str(self.product.pk), 'quantity_1': '2', 'unit_1': 'tray',
        }
        data.update(kwargs)
        return data

    def create(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.url, self.payload())
        self.assertEqual(response.status_code, 302)
        return EggPOSManualPrebooking.objects.get()

    def test_create_no_stock_sales_or_cash(self):
        booking = self.create()
        self.assertEqual(booking.total_eggs, 60)
        self.assertEqual(booking.customer_phone, '03001234567')
        self.assertEqual(booking.channel, 'tiktok')
        self.assertEqual(booking.status, 'awaiting_stock')
        self.assertEqual(EggPOSSale.objects.count(), 0)
        self.assertEqual(EggPOSInventoryLot.objects.count(), 0)
        self.assertEqual(EggPOSManualPrebookingEvent.objects.count(), 1)

    def test_double_submit_not_duplicate(self):
        self.create()
        self.client.post(self.url, self.payload())
        self.assertEqual(EggPOSManualPrebooking.objects.count(), 1)
        self.assertEqual(EggPOSManualPrebookingEvent.objects.count(), 1)

    def test_reconfirmation_requires_stock_arrived_and_customer_yes(self):
        booking = self.create()
        self.client.post(self.url, {'action':'reconfirmed','booking_id':booking.id,
                                    'customer_said_yes':'yes','reconfirmation_channel':'whatsapp'})
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'awaiting_stock')
        self.client.post(self.url, {'action':'stock_arrived','booking_id':booking.id})
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'contact_customer')
        self.client.post(self.url, {'action':'reconfirmed','booking_id':booking.id,
                                    'reconfirmation_channel':'whatsapp'})
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'contact_customer')
        self.client.post(self.url, {'action':'reconfirmed','booking_id':booking.id,
                                    'customer_said_yes':'yes','reconfirmation_channel':'whatsapp'})
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'confirmed')
        self.assertEqual(EggPOSSale.objects.count(), 0)

    def test_complete_needs_manual_sale_reference(self):
        booking = self.create()
        self.client.post(self.url, {'action':'stock_arrived','booking_id':booking.id})
        self.client.post(self.url, {'action':'reconfirmed','booking_id':booking.id,
                                    'customer_said_yes':'yes','reconfirmation_channel':'phone'})
        self.client.post(self.url, {'action':'completed','booking_id':booking.id})
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'confirmed')
        self.client.post(self.url, {'action':'completed','booking_id':booking.id,
                                    'invoice_reference': 'RNE-00095'})
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'completed')
        self.assertEqual(booking.invoice_reference, 'RNE-00095')
        self.assertEqual(EggPOSSale.objects.count(), 0)

    def test_edit_audited_and_cancel_has_reason(self):
        booking = self.create()
        data = self.payload(action='update', booking_id=str(booking.id),
                            customer_name='Customer Facebook', channel='facebook', quantity_1='1')
        self.client.post(self.url, data)
        booking.refresh_from_db()
        self.assertEqual(booking.channel, 'facebook')
        self.assertEqual(booking.total_eggs, 30)
        self.assertTrue(booking.events.filter(action='edited').exists())
        self.client.post(self.url, {'action':'cancelled','booking_id':booking.id,
                                    'cancellation_reason':'Customer declined'})
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'cancelled')
        self.assertTrue(booking.events.filter(action='cancelled').exists())

    def test_non_admin_cannot_read_or_write(self):
        self.client.force_login(self.visitor)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, self.payload()).status_code, 403)
        self.assertEqual(EggPOSManualPrebooking.objects.count(), 0)
