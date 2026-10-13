from django.test import SimpleTestCase
from django.urls import reverse

class RayNoorPublicPagesTests(SimpleTestCase):
    def test_pages_are_public_without_exposing_accounts(self):
        for route in ('raynoor_goats_public','raynoor_poultry_public','raynoor_technology_public','raynoor_investors_public','raynoor_about_public'):
            with self.subTest(route=route):
                response=self.client.get(reverse(route))
                self.assertEqual(response.status_code,200)
                self.assertContains(response,'RayNoor Organic Farms')

    def test_goats_explain_remote_weights_and_expenses(self):
        response=self.client.get(reverse('raynoor_goats_public'))
        self.assertContains(response,'1,000 km')
        self.assertContains(response,'Weight management')
        self.assertContains(response,'Expense visibility')
        self.assertContains(response,'Illustrative dashboard screenshot')
        self.assertContains(response,'Goat investor participation')

    def test_poultry_explains_ai_and_investor_risks(self):
        response=self.client.get(reverse('raynoor_poultry_public'))
        self.assertContains(response,'1,000 km')
        self.assertContains(response,'Poultry investor participation')
        self.assertContains(response,'No guaranteed returns')

    def test_original_homepage_keeps_monitoring_and_new_links(self):
        response=self.client.get(reverse('home'))
        self.assertContains(response,'Environmental Monitoring')
        self.assertContains(response,'Smarter')
        self.assertContains(response,reverse('raynoor_goats_public'))
        self.assertContains(response,reverse('raynoor_poultry_public'))

    def test_investment_information_is_not_application(self):
        response=self.client.get(reverse('raynoor_investors_public'))
        self.assertContains(response,'No guaranteed or fixed returns')
        self.assertContains(response,'does not display live investment opportunities')
