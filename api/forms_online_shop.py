"""Owner-only forms for public egg pack and Lahore delivery setup."""
from django import forms
from api.models.online_shop import OnlineOffer, OnlineDeliveryZone
from api.models.egg_pos import EggPOSProduct


class OnlineOfferSetupForm(forms.ModelForm):
    class Meta:
        model = OnlineOffer
        fields = ('product', 'pack_size', 'title', 'price_per_pack', 'sort_order', 'is_active')
        labels = {
            'product': 'Existing Egg POS product', 'pack_size': 'Eggs per pack',
            'title': 'Retail pack title (optional)', 'price_per_pack': 'Retail price per pack (Rs)',
            'sort_order': 'Display order', 'is_active': 'Show pack in online shop',
        }
        widgets = {
            'price_per_pack': forms.NumberInput(attrs={'min': '0.01', 'step': '0.01'}),
            'sort_order': forms.NumberInput(attrs={'min': '0'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['product'].queryset = EggPOSProduct.objects.all().order_by('name')

    def clean(self):
        cleaned = super().clean()
        product = cleaned.get('product')
        if cleaned.get('is_active') and product is not None and not product.is_active:
            self.add_error('product', 'Activate this product in Egg POS before publishing the pack.')
        return cleaned


class OnlineDeliveryZoneSetupForm(forms.ModelForm):
    class Meta:
        model = OnlineDeliveryZone
        fields = ('name', 'delivery_fee', 'minimum_order', 'own_rider_allowed', 'third_party_allowed', 'is_active')
        labels = {
            'name': 'Lahore area', 'delivery_fee': 'Customer delivery charge (Rs)',
            'minimum_order': 'Minimum egg order subtotal (Rs)',
            'own_rider_allowed': 'Own rider available',
            'third_party_allowed': 'Third-party rider available',
            'is_active': 'Allow checkout in this area',
        }
        widgets = {
            'delivery_fee': forms.NumberInput(attrs={'min': '0', 'step': '0.01'}),
            'minimum_order': forms.NumberInput(attrs={'min': '0', 'step': '0.01'}),
        }

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('is_active') and not (cleaned.get('own_rider_allowed') or cleaned.get('third_party_allowed')):
            self.add_error('is_active', 'Choose at least one delivery method before activating this area.')
        return cleaned
