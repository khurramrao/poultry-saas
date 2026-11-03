"""Optional marketing attribution for Egg POS; not part of stock or financial postings."""
from django.conf import settings
from django.db import models
from django.utils import timezone
from .egg_pos import EggPOSCustomer, EggPOSSale

MARKETING_CHANNELS = (
    ("tiktok", "TikTok"), ("facebook", "Facebook"),
    ("instagram", "Instagram"), ("whatsapp", "WhatsApp"),
    ("website", "Website"), ("phone", "Phone Call"),
    ("direct", "Direct / Walk-in"), ("other", "Other"),
)


class EggPOSMarketingCustomer(models.Model):
    """An identified person, not a phone-only deduplication key.

    Different names sharing a household number are kept separate.
    """
    name = models.CharField(max_length=150)
    name_key = models.CharField(max_length=150)
    phone_key = models.CharField(max_length=20)
    pos_customer = models.ForeignKey(
        EggPOSCustomer, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="marketing_profiles",
    )
    acquired_from = models.CharField(max_length=20, choices=MARKETING_CHANNELS)
    acquired_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="egg_pos_marketing_profiles_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["phone_key", "name_key"], name="ep_marketing_phone_name_uq"),
        ]
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.name} ({self.phone_key}) — {self.get_acquired_from_display()}"


class EggPOSMarketingSale(models.Model):
    sale = models.OneToOneField(
        EggPOSSale, on_delete=models.PROTECT, related_name="marketing_attribution",
    )
    marketing_customer = models.ForeignKey(
        EggPOSMarketingCustomer, null=True, blank=True, on_delete=models.PROTECT,
        related_name="attributed_sales",
    )
    order_source = models.CharField(max_length=20, choices=MARKETING_CHANNELS, default="direct")
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="egg_pos_marketing_sale_sources",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.sale.sale_number}: {self.get_order_source_display()}"
