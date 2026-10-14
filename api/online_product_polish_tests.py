"""Run: python manage.py test api.online_product_polish_tests"""
from django.template import Context, Template
from django.test import SimpleTestCase

from api.templatetags.product_rich_text import product_intro


class ProductIntroTests(SimpleTestCase):
    def test_first_sentence_from_formatted_description(self):
        description = (
            '## RayNoor Farm Fresh Eggs – Tray of 30\n'
            'Stock up on fresh chicken eggs for your household. '
            'Ideal for larger families and home cooking.\n\n'
            '## Product Highlights\n- 30 eggs'
        )
        self.assertEqual(product_intro(description), 'Stock up on fresh chicken eggs for your household.')

    def test_standalone_bold_heading_does_not_enter_summary(self):
        self.assertEqual(product_intro('**Farm Fresh Eggs**\nA convenient 6-egg pack.\n\n- Eggs'),
                         'A convenient 6-egg pack.')

    def test_no_incomplete_ellipsis(self):
        text = 'A whole paragraph with no terminal punctuation'
        self.assertEqual(product_intro(text), text)

    def test_summary_escapes_customer_supplied_html(self):
        result = Template('{% load product_rich_text %}{{ value|product_intro }}').render(
            Context({'value': '<script>alert(1)</script> Enjoy fresh eggs.'})
        )
        self.assertIn('&lt;script&gt;', result)
        self.assertNotIn('<script>', result)

    def test_empty_description(self):
        self.assertEqual(product_intro(''), '')
