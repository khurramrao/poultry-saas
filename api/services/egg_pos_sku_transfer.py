"""Accounting-neutral transfer of *on-hand* eggs between retail POS SKUs."""
from uuid import uuid4

from django.db import transaction
from django.utils import timezone

from api.models.egg_pos import EggPOSInventoryLot, EggPOSProduct, EggPOSPurchase
from api.models.egg_pos_sku_transfer import EggPOSSkuTransfer
from api.services.online_shop import reserved_quantity


@transaction.atomic
def transfer_purchase_lot(*, purchase, source_lot_id, destination_product_id, quantity,
                          actor, reason):
    if not actor.is_authenticated or not actor.is_active or not actor.is_superuser:
        raise ValueError("Only an active RayNoor administrator may transfer purchased stock.")
    purchase = EggPOSPurchase.objects.select_for_update().get(pk=purchase.pk)
    if purchase.is_voided:
        raise ValueError("A voided purchase cannot be reclassified.")
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise ValueError("Please enter a meaningful transfer reason (at least 5 characters).")
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise ValueError("Quantity must be a whole number.")
    if quantity <= 0:
        raise ValueError("Quantity must be greater than zero.")

    # Sales, writeoffs and transfers all lock the same physical lot row.
    # Lock only the inventory lot row. Supplier and purchase are nullable
    # foreign keys; joining them with select_related() creates LEFT OUTER JOINs.
    # PostgreSQL rejects a bare FOR UPDATE against the nullable side of those
    # joins (NotSupportedError). Related objects are fetched normally below.
    source_lot = EggPOSInventoryLot.objects.select_for_update().get(pk=source_lot_id)
    if source_lot.source_type != "purchase" or source_lot.purchase_id != purchase.pk:
        raise ValueError("Select an original purchased lot belonging to this purchase.")
    # Deliberately restrict this workflow to the original supplier purchase
    # item, not a previously reclassified (derived) lot.
    if not hasattr(source_lot, "purchase_item"):
        raise ValueError("Only an original purchase item may be transferred.")
    if source_lot.product.category != "egg":
        raise ValueError("This transfer screen is for egg SKUs only.")
    dest = EggPOSProduct.objects.get(pk=destination_product_id)
    if not dest.is_active or dest.category != "egg":
        raise ValueError("The destination must be an active egg SKU.")
    if dest.pk == source_lot.product_id:
        raise ValueError("Choose a different destination SKU.")
    today = timezone.localdate()
    if source_lot.received_date > today:
        raise ValueError("You cannot transfer a lot that has not yet been received.")
    if source_lot.expiry_date and source_lot.expiry_date < today:
        raise ValueError("An expired lot cannot be transferred into sellable stock.")
    if source_lot.quantity_remaining < quantity:
        raise ValueError(f"Only {source_lot.quantity_remaining} physical units remain in this lot.")

    # Reservations are SKU-level in this POS. Subtract ALL source-SKU
    # reservations conservatively so no promised stock is reclassified.
    reserved = max(0, int(reserved_quantity(source_lot.product_id)))
    safe_free = max(0, int(source_lot.quantity_remaining) - reserved)
    if quantity > safe_free:
        raise ValueError(
            f"Only {safe_free} units can safely be transferred from this lot "
            f"after allowing for {reserved} online-reserved {source_lot.product.name} units."
        )

    destination_lot = EggPOSInventoryLot.objects.create(
        product=dest,
        source_type="purchase",
        supplier=source_lot.supplier,
        purchase=source_lot.purchase,
        # Unique, independent lot with same receipt date for FIFO sorting.
        lot_code=f"SKU-{source_lot.pk}-{uuid4().hex[:12].upper()}",
        received_date=source_lot.received_date,
        expiry_date=source_lot.expiry_date,
        quantity_received=quantity,
        quantity_remaining=quantity,
        unit_cost=source_lot.unit_cost,
        notes=(f"Reclassified from lot {source_lot.lot_code} / "
               f"purchase #{purchase.pk}. No additional supplier purchase."),
    )
    source_lot.quantity_remaining -= quantity
    source_lot.save(update_fields=["quantity_remaining"])
    return EggPOSSkuTransfer.objects.create(
        source_lot=source_lot, destination_lot=destination_lot,
        source_product=source_lot.product, destination_product=dest,
        quantity=quantity, unit_cost_at_transfer=source_lot.unit_cost,
        reason=reason, created_by=actor,
    )


@transaction.atomic
def reverse_sku_transfer(*, transfer_id, actor, reason):
    if not actor.is_authenticated or not actor.is_active or not actor.is_superuser:
        raise ValueError("Only an active RayNoor administrator may reverse a SKU transfer.")
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise ValueError("Enter a meaningful reversal reason (at least 5 characters).")
    transfer = EggPOSSkuTransfer.objects.select_for_update().get(pk=transfer_id)
    if transfer.is_reversed:
        raise ValueError("This SKU transfer is already reversed.")
    lots = {
        lot.id: lot for lot in EggPOSInventoryLot.objects.select_for_update()
        .filter(id__in=[transfer.source_lot_id, transfer.destination_lot_id])
        .order_by("id")
    }
    source = lots[transfer.source_lot_id]
    destination = lots[transfer.destination_lot_id]
    if destination.quantity_remaining != transfer.quantity:
        raise ValueError("Destination stock has been used or moved. It cannot be reversed.")
    if destination.sale_allocations.exists() or destination.writeoffs.exists():
        raise ValueError("Destination lot has sale/write-off history. Reversal is not permitted.")
    if destination.sku_transfers_out.filter(is_reversed=False).exists():
        raise ValueError("Destination has onward SKU transfers. Reverse those first.")
    if int(reserved_quantity(destination.product_id)) > 0:
        raise ValueError("Destination SKU has online reservations; reversal is blocked for safety.")
    source.quantity_remaining += transfer.quantity
    if source.quantity_remaining > source.quantity_received:
        raise ValueError("Source quantity would exceed original receipt; reversal stopped.")
    destination.quantity_remaining = 0
    source.save(update_fields=["quantity_remaining"])
    destination.save(update_fields=["quantity_remaining"])
    transfer.is_reversed = True
    transfer.reversed_by = actor
    transfer.reversed_at = timezone.now()
    transfer.reversal_reason = reason
    transfer.save(update_fields=["is_reversed", "reversed_by", "reversed_at", "reversal_reason"])
    return transfer
