"""Audited, transactional cancellation of an untouched duplicate supplier purchase."""
from django.db import transaction
from django.utils import timezone

from api.models.accounting import JournalEntry
from api.models.egg_pos import EggPOSPurchase
from api.services.accounting import line, post_entry
from api.services.egg_pos_accounting import sync_purchase


@transaction.atomic
def void_supplier_purchase(*, purchase_id, actor, reason):
    """Keep the invoice/line items for audit; delete only unused FIFO lots.

    Uses a separate reversing journal rather than erasing the original purchase
    journal. Duplicate void requests are blocked even with concurrent admins.
    """
    if not actor.is_authenticated or not actor.is_active or not (actor.is_staff or actor.is_superuser):
        raise ValueError("Only Django administrators may void a purchase.")
    reason = (reason or "").strip()
    if len(reason) < 8:
        raise ValueError("Void reason must be at least 8 characters.")

    purchase = (EggPOSPurchase.objects.select_for_update()
                .select_related("supplier", "created_by").get(pk=purchase_id))
    if purchase.is_voided:
        raise ValueError("Purchase has already been voided; no changes made.")

    # Catch every type of linked supplier payment: a paid purchase is not a
    # duplicate that can be cleanly cancelled without refund reconciliation.
    if purchase.payments.exists():
        raise ValueError("Purchase has supplier payments. Reconcile/refund them before voiding.")

    items = list(purchase.items.select_related("lot", "product").order_by("pk"))
    if not items:
        raise ValueError("Purchase has no item records; use a manual accounting correction.")
    lots = []
    seen = set()
    for item in items:
        if not item.lot_id:
            raise ValueError("Purchase lot is missing; cannot safely reverse stock.")
        lot = item.lot
        if lot.id in seen:
            raise ValueError("Purchase has duplicate FIFO lot associations; manual review required.")
        seen.add(lot.id)
        # Lock actual lot row, not just item relation; safe against a sale
        # allocating stock concurrently during confirmation.
        lot = type(lot).objects.select_for_update().get(pk=lot.id)
        if lot.source_type != "purchase" or lot.purchase_id != purchase.pk:
            raise ValueError("FIFO lot does not belong to this purchase.")
        if int(lot.quantity_received) != int(item.quantity) or int(lot.quantity_remaining) != int(lot.quantity_received):
            raise ValueError("Some eggs were sold, reserved or stock-adjusted. Cannot void this purchase.")
        if lot.sale_allocations.exists():
            raise ValueError("FIFO sales already use this lot. Reverse those sales using the sale workflow first.")
        lots.append(lot)

    # Guard against non-purchase lots linked to this invoice that may not be
    # represented by an item, and against partially deleted inconsistent data.
    from api.models.egg_pos import EggPOSInventoryLot
    if EggPOSInventoryLot.objects.filter(purchase=purchase).count() != len(lots):
        raise ValueError("Purchase has unmatched FIFO lots; manual review required.")

    # Online customers reserve stock by product, not by FIFO lot. Prevent
    # canceling a duplicate if it would leave insufficient stock for confirmed
    # (or unexpired reserved) customer orders. Lock all eligible lots so this
    # check cannot race against POS FIFO usage / online checkout.
    from api.services.online_shop import gross_available, reserved_quantity
    removed_by_product = {}
    for lot in lots:
        if lot.received_date <= timezone.localdate() and (not lot.expiry_date or lot.expiry_date >= timezone.localdate()):
            removed_by_product[lot.product_id] = removed_by_product.get(lot.product_id, 0) + int(lot.quantity_remaining)
    for product_id, removed in sorted(removed_by_product.items()):
        available_after_void = gross_available(product_id, lock=True) - removed
        if available_after_void < reserved_quantity(product_id):
            raise ValueError("This purchase is needed for pending online orders. Complete or cancel reservations first.")

    original_key = f"egg_pos:purchase:{purchase.id}"
    original = JournalEntry.objects.select_for_update().filter(source_key=original_key).first()
    if original is None:
        sync_purchase(purchase)
        original = JournalEntry.objects.select_for_update().get(source_key=original_key)
    journal_lines = list(original.lines.select_related("account"))
    if not journal_lines:
        raise ValueError("Purchase journal has no lines; manual review required.")
    reversal_lines = [
        line(
            row.account.code,
            debit=row.credit,
            credit=row.debit,
            description=f"Void duplicate purchase #{purchase.id}",
            party_type=row.party_type,
            party_id=row.party_id,
            party_name=row.party_name,
        )
        for row in journal_lines
    ]
    post_entry(
        source_key=f"egg_pos:purchase:void:{purchase.id}",
        entry_date=timezone.localdate(),
        reference=f"PUR-VOID-{purchase.id:05d}",
        memo=f"Void duplicate supplier purchase #{purchase.id}: {reason}"[:255],
        source_type="egg_pos_purchase_void",
        source_id=purchase.id,
        created_by=actor,
        lines=reversal_lines,
    )
    # Deleting a lot is blocked at DB level if another model has a protected
    # reference. Atomic transaction rolls back all journal and stock edits.
    for item, lot in zip(items, lots):
        item.lot = None
        item.save(update_fields=["lot"])
        lot.delete()
    purchase.is_voided = True
    purchase.voided_at = timezone.now()
    purchase.voided_by = actor
    purchase.void_reason = reason[:500]
    purchase.save(update_fields=["is_voided", "void_reason", "voided_at", "voided_by"])
    return purchase
