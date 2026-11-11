"""Manual retail pre-bookings; no sale, stock or general-ledger postings."""
import uuid
from decimal import Decimal
from django.conf import settings
from django.db import models
from django.utils import timezone
from .egg_pos import EggPOSProduct, EggPOSCustomer


CHANNELS = (
    ("tiktok", "TikTok"), ("facebook", "Facebook"),
    ("instagram", "Instagram"), ("whatsapp", "WhatsApp"),
    ("phone", "Phone Call"), ("other", "Other"),
)
UNITS = (("egg", "Eggs"), ("pack6", "6-Egg Packs"),
         ("dozen", "Dozens (12)"), ("tray", "Trays (30)"),
         ("jar", "Sealed Jars"))
UNIT_MULTIPLIER = {"egg": 1, "pack6": 6, "dozen": 12, "tray": 30, "jar": 1}
EGG_UNITS = frozenset({"egg", "pack6", "dozen", "tray"})
STATUSES = (
    ("awaiting_stock", "Awaiting Stock"),
    ("contact_customer", "Stock Arrived — Reconfirm"),
    ("confirmed", "Customer Reconfirmed"),
    ("completed", "Completed"),
    ("cancelled", "Cancelled / Declined"),
)


class EggPOSManualPrebooking(models.Model):
    # Optional customer link: existing free-text bookings remain valid after migration.
    customer = models.ForeignKey(EggPOSCustomer, on_delete=models.SET_NULL, null=True,
                                 blank=True, related_name="manual_prebookings")
    customer_name = models.CharField(max_length=150)
    customer_phone = models.CharField(max_length=40)
    channel = models.CharField(max_length=20, choices=CHANNELS)
    delivery_area = models.CharField(max_length=180, blank=True)
    expected_date = models.DateField(null=True, blank=True)
    # Local delivery-window times; never imply stock or delivery confirmation.
    delivery_time_from = models.TimeField(null=True, blank=True)
    delivery_time_to = models.TimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=24, choices=STATUSES, default="awaiting_stock", db_index=True)
    stock_arrived_at = models.DateTimeField(null=True, blank=True)
    reconfirmed_at = models.DateTimeField(null=True, blank=True)
    reconfirmation_channel = models.CharField(max_length=20, choices=CHANNELS, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    invoice_reference = models.CharField(max_length=80, blank=True,
                                         help_text="Manual POS invoice/reference only; no automatic sale link")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   related_name="egg_manual_prebookings_created")
    request_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [models.Index(fields=["status", "created_at"], name="ep_prebook_status_idx")]

    @property
    def booking_number(self):
        return f"RNPRE-{self.pk:06d}" if self.pk else "RNPRE-NEW"

    @property
    def total_eggs(self):
        # Prefetched lines are used when displayed in the booking list.
        return sum(item.eggs_count for item in self.lines.all())

    @property
    def total_jars(self):
        return sum(item.jars_count for item in self.lines.all())

    @property
    def total_items_display(self):
        """Do not count retail jars as eggs or mix unlike units."""
        eggs, jars = self.total_eggs, self.total_jars
        sections = []
        if eggs:
            sections.append(f"{eggs:,} eggs")
        if jars:
            sections.append(f"{jars:,} {'jar' if jars == 1 else 'jars'}")
        return " · ".join(sections) or "Check product units"

    @property
    def delivery_window_display(self):
        if not (self.delivery_time_from and self.delivery_time_to):
            return ""
        start = self.delivery_time_from.strftime("%I:%M %p").lstrip("0")
        end = self.delivery_time_to.strftime("%I:%M %p").lstrip("0")
        return f"{start} – {end}"

    @property
    def quoted_items_total(self):
        """None for unpriced/partially priced bookings; never treat missing as Rs 0."""
        lines = list(self.lines.all())
        if not lines or any(line.quoted_unit_price is None for line in lines):
            return None
        return sum((line.quoted_line_total for line in lines), Decimal("0.00"))

    @property
    def whatsapp_number(self):
        raw = "".join(c for c in (self.customer_phone or "") if c.isdigit())
        if raw.startswith("03") and len(raw) == 11:
            return "92" + raw[1:]
        if raw.startswith("0092"):
            return raw[2:]
        return raw

    def __str__(self):
        return f"{self.booking_number} · {self.customer_name}"


class EggPOSManualPrebookingLine(models.Model):
    booking = models.ForeignKey(EggPOSManualPrebooking, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(EggPOSProduct, on_delete=models.PROTECT,
                                related_name="manual_prebooking_lines")
    quantity = models.PositiveIntegerField()
    unit = models.CharField(max_length=12, choices=UNITS, default="tray")
    # The agreed/quoted unit rate is a snapshot, NOT a POS invoice selling price.
    quoted_unit_price = models.DecimalField(max_digits=10, decimal_places=2,
                                            null=True, blank=True)

    class Meta:
        ordering = ("id",)

    @property
    def eggs_count(self):
        if not self.product.is_egg or self.unit not in EGG_UNITS:
            return 0
        return self.quantity * UNIT_MULTIPLIER[self.unit]

    @property
    def jars_count(self):
        return self.quantity if not self.product.is_egg and self.unit == "jar" else 0

    @property
    def quoted_line_total(self):
        if self.quoted_unit_price is None:
            return None
        return self.quoted_unit_price * self.quantity

    @property
    def quoted_rate_unit(self):
        return {"egg": "egg", "pack6": "6-egg pack", "dozen": "dozen", "tray": "tray", "jar": "jar"}.get(self.unit, self.unit)

    def __str__(self):
        return f"{self.product.name}: {self.quantity} {self.get_unit_display()}"


class EggPOSManualPrebookingEvent(models.Model):
    booking = models.ForeignKey(EggPOSManualPrebooking, on_delete=models.CASCADE, related_name="events")
    action = models.CharField(max_length=35)
    details = models.TextField(blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                              related_name="egg_manual_prebooking_events")
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ("created_at", "id")
