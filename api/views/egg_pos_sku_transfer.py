"""Administrator-only purchase SKU reclassification UI."""
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import authenticate
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from api.models.egg_pos import EggPOSProduct, EggPOSPurchase
from api.models.egg_pos_sku_transfer import EggPOSSkuTransfer
from api.services.egg_pos_sku_transfer import transfer_purchase_lot, reverse_sku_transfer


def _valid_admin_password(request):
    user = request.user
    if not user.is_authenticated or not user.is_active or not user.is_superuser:
        return False
    password = request.POST.get("admin_password") or ""
    verified = authenticate(request, username=user.get_username(), password=password)
    return bool(verified and verified.pk == user.pk and verified.is_active and verified.is_superuser)


@login_required
@require_http_methods(["GET", "POST"])
def purchase_sku_transfer(request, purchase_id):
    if not request.user.is_active or not request.user.is_superuser:
        messages.error(request, "Only RayNoor administrators can reclassify purchased stock.")
        return redirect("egg_pos_purchase_list")
    purchase = get_object_or_404(EggPOSPurchase.objects.select_related("supplier"), pk=purchase_id)
    if purchase.is_voided:
        messages.error(request, "This purchase is voided.")
        return redirect("egg_pos_purchase_list")

    item_list = list(purchase.items.select_related("product", "lot").order_by("id"))
    destinations = EggPOSProduct.objects.filter(category="egg", is_active=True).order_by("name")
    transfers = list(
        EggPOSSkuTransfer.objects.filter(source_lot__purchase_id=purchase_id)
        .select_related("source_product", "destination_product", "source_lot", "destination_lot", "created_by")
    )
    for item in item_list:
        item.available_now = int(item.lot.quantity_remaining) if item.lot_id else 0
        item.moved_qty = sum(t.quantity for t in transfers if t.source_lot_id == item.lot_id and not t.is_reversed)

    if request.method == "POST":
        try:
            if not _valid_admin_password(request):
                raise ValueError("Your administrator password is incorrect, or admin permission is missing.")
            source_id = int(request.POST.get("source_lot_id") or 0)
            source = next((i for i in item_list if i.lot_id == source_id), None)
            if not source:
                raise ValueError("Select an original purchase lot from this purchase.")
            target_id = int(request.POST.get("destination_product_id") or 0)
            if not destinations.filter(id=target_id).exists():
                raise ValueError("Select an active destination egg SKU.")
            movement = transfer_purchase_lot(
                purchase=purchase, source_lot_id=source_id,
                destination_product_id=target_id,
                quantity=request.POST.get("quantity"), actor=request.user,
                reason=request.POST.get("reason"),
            )
            messages.success(request,
                f"{movement.reference} posted: {movement.quantity} eggs moved from "
                f"{movement.source_product.sku} to {movement.destination_product.sku}. "
                "Original supplier invoice and GL value were not changed.")
            return redirect("egg_pos_sku_transfer", purchase_id=purchase.id)
        except (ValueError, TypeError, EggPOSProduct.DoesNotExist) as error:
            messages.error(request, str(error))

    return render(request, "api/egg_pos_sku_transfer.html", {
        "purchase": purchase,
        "items": item_list,
        "destinations": destinations,
        "transfers": transfers,
    })


@login_required
@require_POST
def reverse_purchase_sku_transfer(request, purchase_id, transfer_id):
    if not _valid_admin_password(request):
        messages.error(request, "Administrator password verification failed.")
        return redirect("egg_pos_sku_transfer", purchase_id=purchase_id)
    transfer = get_object_or_404(EggPOSSkuTransfer, id=transfer_id,
                                 source_lot__purchase_id=purchase_id)
    try:
        reverse_sku_transfer(transfer_id=transfer.id, actor=request.user,
                             reason=request.POST.get("reversal_reason"))
        messages.success(request, f"{transfer.reference} reversed. Source stock restored.")
    except ValueError as error:
        messages.error(request, str(error))
    return redirect("egg_pos_sku_transfer", purchase_id=purchase_id)
