"""Owner-only forms for public egg pack and Lahore delivery setup."""
from django import forms
from django.utils import timezone
from api.services.online_offer_images import prepare_offer_photo
from api.models.online_shop import OnlineOffer, OnlineDeliveryZone
from api.models.egg_pos import EggPOSProduct


class OnlineOfferSetupForm(forms.ModelForm):
    photo_upload = forms.FileField(required=False, label='Upload egg pack photo (JPG / PNG / WebP)')
    remove_photo = forms.BooleanField(required=False, label='Remove current photo')

    class Meta:
        model = OnlineOffer
        fields = ('product', 'pack_size', 'title', 'description', 'seo_title', 'seo_description', 'egg_weight_label', 'price_per_pack', 'photo_fit', 'photo_position', 'photo_alt', 'sort_order', 'is_active')
        labels = {
            'product': 'Existing Egg POS product', 'pack_size': 'Eggs per pack',
            'title': 'Retail pack title (optional)', 'description': 'Product description (formatted)',
            'seo_title': 'SEO title (optional)', 'seo_description': 'Meta description (optional)',
            'egg_weight_label': 'Egg weight per piece (optional)',
            'price_per_pack': 'Retail price per pack (Rs)',
            'photo_fit': 'Photo display', 'photo_position': 'Photo alignment',
            'photo_alt': 'Image description (HTML alt text / SEO)',
            'sort_order': 'Display order', 'is_active': 'Show pack in online shop',
        }
        widgets = {
            'price_per_pack': forms.NumberInput(attrs={'min': '0.01', 'step': '0.01'}),
            'sort_order': forms.NumberInput(attrs={'min': '0'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['product'].queryset = EggPOSProduct.objects.all().order_by('name')
        # Accept older admin/API forms that do not post the new optional image controls.
        self.fields['photo_fit'].required = False
        self.fields['photo_position'].required = False

    def clean_photo_fit(self):
        return self.cleaned_data.get('photo_fit') or 'contain'

    def clean_photo_position(self):
        return self.cleaned_data.get('photo_position') or 'center'

    def clean_photo_upload(self):
        file = self.cleaned_data.get('photo_upload')
        if file is None:
            return None
        return prepare_offer_photo(file)

    def save(self, commit=True):
        offer = super().save(commit=False)
        photo = self.cleaned_data.get('photo_upload')
        if photo:
            offer.photo_data = photo
            offer.photo_mime = 'image/jpeg'
            offer.photo_updated_at = timezone.now()
        elif self.cleaned_data.get('remove_photo'):
            offer.photo_data = None
            offer.photo_mime = ''
            offer.photo_updated_at = timezone.now()
        if commit:
            offer.save()
            self.save_m2m()
        return offer

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('photo_upload') and cleaned.get('remove_photo'):
            self.add_error('remove_photo', 'Choose either a replacement photo or removal, not both.')
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
