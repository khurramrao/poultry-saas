"""Focused authentication tests: python manage.py test api.case_insensitive_login_tests"""
from django.contrib.auth import authenticate, get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(AUTHENTICATION_BACKENDS=[
    'api.auth_backends.UniqueCaseInsensitiveUsernameBackend',
])
class CaseInsensitiveLoginTests(TestCase):
    def test_login_does_not_depend_on_capitalization(self):
        User = get_user_model()
        person = User.objects.create_user(username='HusnainInvestor', password='TestPass9876!')
        for typed in ('HusnainInvestor', 'husnaininvestor', 'HUSNAININVESTOR'):
            authenticated = authenticate(username=typed, password='TestPass9876!')
            self.assertEqual(authenticated.pk, person.pk)

    def test_wrong_password_still_rejected(self):
        User = get_user_model()
        User.objects.create_user(username='GoatInvestorABC', password='TestPass9876!')
        self.assertIsNone(authenticate(username='goatinvestorabc', password='not-the-password'))

    def test_inactive_account_still_rejected(self):
        User = get_user_model()
        User.objects.create_user(username='OwnerTemporary', password='TestPass9876!', is_active=False)
        self.assertIsNone(authenticate(username='ownertemporary', password='TestPass9876!'))

    def test_ambiguous_case_insensitive_match_rejected(self):
        User = get_user_model()
        User.objects.create_user(username='HusnainOne', password='TestPass9876!')
        User.objects.create_user(username='husnainone', password='TestPass9876!')
        self.assertIsNone(authenticate(username='HUSNAINONE', password='TestPass9876!'))
        self.assertIsNotNone(authenticate(username='HusnainOne', password='TestPass9876!'))

    def test_existing_login_page_works(self):
        User = get_user_model()
        User.objects.create_user(username='RayStaffTest', password='TestPass9876!')
        response = self.client.post(reverse('login'), {
            'username': 'raystafftest', 'password': 'TestPass9876!',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session.get('_auth_user_backend'),
                         'api.auth_backends.UniqueCaseInsensitiveUsernameBackend')
