from django.contrib import admin
from api.models.online_shop import OnlineOffer, OnlineDeliveryZone, OnlineOrder, OnlineOrderItem, OnlineRetailAccount

@admin.register(OnlineOffer)
class OnlineOfferAdmin(admin.ModelAdmin):
    list_display = ('product', 'pack_size', 'price_per_pack', 'is_active')
    list_editable = ('price_per_pack', 'is_active')
    list_filter = ('is_active',)

@admin.register(OnlineDeliveryZone)
class OnlineDeliveryZoneAdmin(admin.ModelAdmin):
    list_display = ('name','delivery_fee','minimum_order','is_active','own_rider_allowed','third_party_allowed')
    list_editable = ('delivery_fee','minimum_order','is_active','own_rider_allowed','third_party_allowed')

class OrderItemInline(admin.TabularInline):
    model = OnlineOrderItem
    extra = 0
    readonly_fields = ('offer','product','product_name','pack_size','pack_count','egg_quantity','pack_price','line_total')
    can_delete = False

@admin.register(OnlineOrder)
class OnlineOrderAdmin(admin.ModelAdmin):
    list_display = ('order_number','customer_name','customer_phone','status','total','created_at')
    search_fields = ('order_number','customer_phone','customer_name')
    inlines = (OrderItemInline,)
    readonly_fields = ('order_number','token','pos_sale','subtotal','delivery_fee','total','reserved_until','cod_settled_at')
    # Status transitions MUST use Online Order Management (for stock, COGS and payments).
    def get_readonly_fields(self, request, obj=None):
        return tuple(f.name for f in self.model._meta.fields) if obj else self.readonly_fields
    def has_add_permission(self, request): return False
    def has_delete_permission(self, request, obj=None): return False

@admin.register(OnlineRetailAccount)
class OnlineRetailAccountAdmin(admin.ModelAdmin):
    list_display = ('phone','name','created_at')
    readonly_fields = ('name','phone','email','password_hash','created_at')
    def has_add_permission(self,request):return False
    def has_change_permission(self,request,obj=None):return False
