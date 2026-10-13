from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from api.models.goats import Goat, GoatWeightRecord, GoatCostEntry, GoatCostAllocation
from api.models.investors import InvestorProfile
from api.models.sensor import Shed


class GoatDashboardUITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('goat_admin', 'goatadmin@example.test', 'testpassword123')
        cls.investor_a = User.objects.create_user('investor_goat_a', password='testpassword123')
        cls.investor_b = User.objects.create_user('investor_goat_b', password='testpassword123')
        InvestorProfile.objects.create(user=cls.investor_a)
        InvestorProfile.objects.create(user=cls.investor_b)
        cls.shed = Shed.objects.create(name='Goat Shed', shed_type='goat')
        cls.goat_a = Goat.objects.create(
            name='Sultan', breed='Makhi Cheeni', sex='male',
            owner=cls.investor_a, shed=cls.shed,
            purchase_cost=Decimal('40000'), purchase_weight_kg=Decimal('20.00'),
            purchase_date=timezone.localdate() - timedelta(days=30),
        )
        cls.goat_b = Goat.objects.create(
            name='Private Goat', breed='Beetal', sex='female', owner=cls.investor_b,
            shed=cls.shed, purchase_cost=Decimal('30000'),
        )
        GoatWeightRecord.objects.create(goat=cls.goat_a, weight_kg=Decimal('20'),
                                        record_date=timezone.localdate() - timedelta(days=25))
        GoatWeightRecord.objects.create(goat=cls.goat_a, weight_kg=Decimal('22.50'),
                                        record_date=timezone.localdate() - timedelta(days=5))
        feed = GoatCostEntry.objects.create(shed=cls.shed, category='feed', amount=Decimal('4400'),
                                             title='Farm feed allocation', created_by=cls.admin)
        GoatCostAllocation.objects.create(goat=cls.goat_a, cost_entry=feed,
                                          owner_snapshot=cls.investor_a, amount=Decimal('4400'))

    def test_admin_detail_shows_recorded_chart_and_costs(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('goat_detail', args=[self.goat_a.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Weight History')
        self.assertContains(response, 'Cost composition')
        self.assertContains(response, '2.50')
        self.assertContains(response, 'Sultan')
        self.assertEqual(len(response.context['weight_chart']['points']), 2)
        self.assertEqual(response.context['finance']['total_cost'], Decimal('44400.00'))

    def test_investor_sees_only_own_goat_read_only(self):
        self.client.force_login(self.investor_a)
        dashboard = self.client.get(reverse('goat_dashboard'))
        self.assertEqual(dashboard.status_code, 200)
        self.assertContains(dashboard, 'Investor read-only access')
        self.assertContains(dashboard, 'Sultan')
        self.assertNotContains(dashboard, 'Private Goat')
        detail = self.client.get(reverse('goat_detail', args=[self.goat_a.id]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, 'Private investor view')
        self.assertNotContains(detail, 'Save Photo')
        self.assertNotContains(detail, 'Record Sale')
        other = self.client.get(reverse('goat_detail', args=[self.goat_b.id]))
        self.assertEqual(other.status_code, 302)

    def test_photo_requires_authorization_and_is_private(self):
        image = b'\x89PNG\r\n\x1a\n' + b'fake-png-bytes-for-signature-tests'
        self.client.force_login(self.admin)
        upload_url = reverse('update_goat_photo', args=[self.goat_a.id])
        result = self.client.post(upload_url, {
            'photo_action': 'upload',
            'goat_photo': SimpleUploadedFile('goat.png', image, content_type='image/png'),
        })
        self.assertEqual(result.status_code, 302)
        self.goat_a.refresh_from_db()
        self.assertEqual(self.goat_a.photo_mime, 'image/png')
        url = reverse('goat_photo', args=[self.goat_a.id])
        get_photo = self.client.get(url)
        self.assertEqual(get_photo.status_code, 200)
        self.assertEqual(get_photo['Cache-Control'], 'private, no-store')
        self.assertEqual(get_photo.content, image)
        self.client.force_login(self.investor_b)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.investor_a)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.post(upload_url, {'photo_action': 'remove'}).status_code, 403)
        self.goat_a.refresh_from_db()
        self.assertEqual(self.goat_a.photo_mime, 'image/png')

    def test_invalid_photo_rejected(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('update_goat_photo', args=[self.goat_a.id]), {
            'photo_action': 'upload',
            'goat_photo': SimpleUploadedFile('bad.svg', b'<svg>unsafe</svg>', content_type='image/svg+xml'),
        })
        self.goat_a.refresh_from_db()
        self.assertFalse(self.goat_a.photo_data)

    def test_no_weight_chart_without_two_readings(self):
        self.client.force_login(self.admin)
        res = self.client.get(reverse('goat_detail', args=[self.goat_b.id]))
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.context['weight_chart'])
        self.assertContains(res, 'No weight readings yet')

    def test_photo_removal(self):
        self.client.force_login(self.admin)
        self.goat_a.photo_data = b'\xff\xd8\xffphoto'
        self.goat_a.photo_mime = 'image/jpeg'
        self.goat_a.save(update_fields=['photo_data','photo_mime'])
        res = self.client.post(reverse('update_goat_photo', args=[self.goat_a.id]), {'photo_action':'remove'})
        self.assertEqual(res.status_code, 302)
        self.goat_a.refresh_from_db()
        self.assertFalse(self.goat_a.photo_data)
        self.assertEqual(self.client.get(reverse('goat_photo', args=[self.goat_a.id])).status_code, 404)
