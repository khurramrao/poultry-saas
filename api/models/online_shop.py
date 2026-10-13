"""Lahore retail web orders. Separate from Django staff and Egg POS wholesale customers."""
import secrets
from decimal import Decimal
from django.conf import settings
from django.db import models
from django.core.validators import MinValueValidator
from django.utils import timezone


def tracking_secret():
    return secrets.token_urlsafe(24)


class OnlineRetailAccount(models.Model):
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=15, unique=True)
    email = models.EmailField(blank=True)
    password_hash = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.phone


class OnlineOffer(models.Model):
    product = models.ForeignKey('api.EggPOSProduct', on_delete=models.PROTECT, related_name='online_offers')
    pack_size = models.PositiveSmallIntegerField(choices=[(6, '6 eggs'), (12, '12 eggs'), (30, '30 eggs')])
    price_per_pack = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    title = models.CharField(max_length=160, blank=True)
    is_active = models.BooleanField(default=False)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ('sort_order', 'id')
        constraints = [models.UniqueConstraint(fields=['product', 'pack_size'], name='unique_online_egg_pack')]

    def __str__(self):
        return self.title or f'{self.product.name} · {self.pack_size} eggs'


class OnlineDeliveryZone(models.Model):
    name = models.CharField(max_length=100, unique=True)
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0'))])
    minimum_order = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'), validators=[MinValueValidator(Decimal('0'))])
    own_rider_allowed = models.BooleanField(default=True)
    third_party_allowed = models.BooleanField(default=True)
    is_active = models.BooleanField(default=False)

    class Meta:
        ordering = ('name',)

    def __str__(self):
        return self.name


class OnlineOrder(models.Model):
    RESERVED = 'reserved'
    CONFIRMED = 'confirmed'
    PACKED = 'packed'
    DISPATCHED = 'dispatched'
    DELIVERED = 'delivered'
    CANCELLED = 'cancelled'
    EXPIRED = 'expired'
    STATUS_CHOICES = [
        (RESERVED, 'Awaiting confirmation'), (CONFIRMED, 'Confirmed'),
        (PACKED, 'Packed'), (DISPATCHED, 'Out for delivery'),
        (DELIVERED, 'Delivered'), (CANCELLED, 'Cancelled'), (EXPIRED, 'Expired'),
    ]
    account = models.ForeignKey(OnlineRetailAccount, null=True, blank=True, on_delete=models.SET_NULL, related_name='orders')
    token = models.CharField(max_length=64, unique=True, default=tracking_secret, editable=False)
    order_number = models.CharField(max_length=30, unique=True, null=True, blank=True)
    customer_name = models.CharField(max_length=120)
    customer_phone = models.CharField(max_length=15)
    address = models.TextField()
    zone = models.ForeignKey(OnlineDeliveryZone, on_delete=models.PROTECT)
    note = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=RESERVED)
    reserved_until = models.DateTimeField(null=True, blank=True)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2)
    delivery_fee = models.DecimalField(max_digits=12, decimal_places=2)
    total = models.DecimalField(max_digits=14, decimal_places=2)
    delivery_method = models.CharField(max_length=20, choices=[('own','Own rider'),('third','Third party')], blank=True)
    rider_name = models.CharField(max_length=120, blank=True)
    tracking_reference = models.CharField(max_length=120, blank=True)
    delivery_cost = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    pos_sale = models.OneToOneField('api.EggPOSSale', null=True, blank=True, on_delete=models.PROTECT, related_name='online_order')
    cod_settled_at = models.DateTimeField(null=True, blank=True)
    cancelled_reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-created_at', '-id')
        indexes = [models.Index(fields=['status', 'reserved_until'])]

    @property
    def is_reserving(self):
        return self.status in (self.CONFIRMED, self.PACKED, self.DISPATCHED) or (
            self.status == self.RESERVED and self.reserved_until and self.reserved_until > timezone.now()
        )

    def __str__(self):
        return self.order_number or f'Online #{self.pk or "new"}'


class OnlineOrderItem(models.Model):
    order = models.ForeignKey(OnlineOrder, on_delete=models.CASCADE, related_name='items')
    offer = models.ForeignKey(OnlineOffer, on_delete=models.PROTECT)
    product = models.ForeignKey('api.EggPOSProduct', on_delete=models.PROTECT)
    product_name = models.CharField(max_length=160)
    pack_size = models.PositiveSmallIntegerField()
    pack_count = models.PositiveSmallIntegerField()
    egg_quantity = models.PositiveIntegerField()
    pack_price = models.DecimalField(max_digits=12, decimal_places=2)
    line_total = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ('id',)

class OnlineOrderEvent(models.Model):
    """Immutable history for admin actions, independent of POS invoice audit."""
    order = models.ForeignKey(OnlineOrder, on_delete=models.CASCADE, related_name='events')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    event = models.CharField(max_length=40)
    previous_status = models.CharField(max_length=20, blank=True)
    new_status = models.CharField(max_length=20)
    details = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at','-id')
