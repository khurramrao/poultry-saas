"""FIFO lot stock write-offs: auditable, transaction-safe and no cash movement.

Only physical *retail POS* inventory is affected. Eggs still at the farm are
managed separately in Egg Management; never enter a farm loss twice.
"""
from decimal import Decimal
from uuid import UUID, uuid4

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from api.models.egg_pos import EggPOSInventoryLot, EggPOSInventoryWriteOff
from api.services.accounting import line, money, post_entry
from api.services.egg_pos_mixed_sources import (
    packed_source_reserved, primary_id, source_ids,
)
from api.services.online_shop import reserved_quantity


def _eligible(lot, today):
    return lot.received_date <= today and (not lot.expiry_date or lot.expiry_date >= today)


def _journal_loss(writeoff, *, reverse=False, user=None):
    amount = writeoff.total_cost
    account = "5010" if writeoff.classification == "routine" else "6150"
    code = f"egg_pos:wastage:{'reverse:' if reverse else ''}{writeoff.id}"
    reference = writeoff.reference + ("-REV" if reverse else "")
    debit, credit = (("1200", account) if reverse else (account, "1200"))
    return post_entry(
        source_key=code,
        entry_date=(timezone.localtime(writeoff.voided_at).date()
                    if reverse and writeoff.voided_at else
                    (timezone.localdate() if reverse else writeoff.writeoff_date)),
        reference=reference,
        memo=("Void loss - " if reverse else "Retail inventory loss - ")
             + f"{writeoff.get_reason_display()} ({writeoff.lot.product.sku})",
        source_type="retail_wastage_reversal" if reverse else "retail_inventory_wastage",
        source_id=writeoff.id,
        created_by=user or writeoff.recorded_by,
        lines=[
            line(debit, debit=amount, description="Inventory loss correction" if reverse else writeoff.get_reason_display()),
            line(credit, credit=amount, description="FIFO inventory restored" if reverse else "FIFO inventory written off"),
        ],
    )


@transaction.atomic
def record_writeoff(*, lot_id, quantity, reason, classification, actor, notes="", submission_token=None):
    if not actor or not actor.pk:
        raise ValueError("A signed-in user is required.")
    try:
        quantity = int(quantity)
    except (TypeError, ValueError) as exc:
        raise ValueError("Enter a whole-number quantity.") from exc
    if quantity < 1:
        raise ValueError("Damage quantity must be at least 1.")
    valid_reason = dict(EggPOSInventoryWriteOff.REASONS)
    valid_class = dict(EggPOSInventoryWriteOff.CLASSES)
    if reason not in valid_reason or classification not in valid_class:
        raise ValueError("Choose a valid reason and accounting classification.")
    notes = (notes or "").strip()
    if classification == "exceptional" and len(notes) < 8:
        raise ValueError("Explain the exceptional loss (at least 8 characters).")
    if len(notes) > 1000:
        raise ValueError("Notes must be 1,000 characters or fewer.")
    if submission_token:
        try:
            token = UUID(str(submission_token))
        except (TypeError, ValueError) as exc:
            raise ValueError("Form token is invalid. Refresh and try again.") from exc
        if EggPOSInventoryWriteOff.objects.filter(submission_token=token).exists():
            raise ValueError("This damage entry has already been submitted. It was not deducted twice.")
    else:
        token = uuid4()

    # Lock *every* lot in this selling group, in a consistent order. Online
    # checkout holds the same locks while checking availability, preventing
    # the two workflows from claiming the same inventory simultaneously.
    try:
        info = EggPOSInventoryLot.objects.only("product_id").get(pk=lot_id)
    except (EggPOSInventoryLot.DoesNotExist, TypeError, ValueError) as exc:
        raise ValueError("Choose a valid inventory lot.") from exc
    group_id = primary_id(info.product_id)
    lots = list(EggPOSInventoryLot.objects.select_for_update().filter(
        product_id__in=source_ids(group_id), quantity_remaining__gt=0,
    ).order_by("id"))
    selected = next((lot for lot in lots if lot.pk == int(lot_id)), None)
    if selected is None:
        raise ValueError("This lot has no stock remaining.")
    today = timezone.localdate()
    if selected.received_date > today:
        raise ValueError("Cannot write off stock before its received date.")
    if quantity > selected.quantity_remaining:
        raise ValueError(f"Only {selected.quantity_remaining} physical units remain in this lot.")
    if reason == "expired" and (not selected.expiry_date or selected.expiry_date >= today):
        raise ValueError("This lot is not expired. Use Spoilage if it failed quality checks early.")

    # Only sellable stock backs online orders. Expired inventory is already held
    # out of sale, so removing it does not affect customer reservations.
    if _eligible(selected, today):
        eligible = [row for row in lots if _eligible(row, today)]
        group_gross = sum(row.quantity_remaining for row in eligible)
        source_gross = sum(row.quantity_remaining for row in eligible if row.product_id == selected.product_id)
        if group_gross - quantity < reserved_quantity(group_id):
            raise ValueError("Cannot write off stock reserved for online customers. Cancel or fulfill those orders first.")
        if source_gross - quantity < packed_source_reserved(selected.product_id):
            raise ValueError("Some of this SKU is allocated to packed online orders. Cannot write it off.")

    # The costing snapshot never changes if vendor costs are edited later.
    total_cost = money(Decimal(quantity) * selected.unit_cost)
    writeoff = EggPOSInventoryWriteOff.objects.create(
        submission_token=token, lot=selected, quantity=quantity,
        unit_cost=selected.unit_cost, total_cost=total_cost,
        reason=reason, classification=classification, notes=notes,
        recorded_by=actor, writeoff_date=today,
    )
    selected.quantity_remaining -= quantity
    selected.save(update_fields=["quantity_remaining"])
    _journal_loss(writeoff)
    return writeoff


@transaction.atomic
def void_writeoff(*, writeoff_id, actor, reason):
    if not actor or not actor.is_active or not (actor.is_superuser or actor.is_staff):
        raise ValueError("Only an administrator can reverse a stock loss.")
    reason = (reason or "").strip()
    if len(reason) < 10:
        raise ValueError("Explain the correction (at least 10 characters).")
    if len(reason) > 500:
        raise ValueError("Correction reason is too long.")
    writeoff = EggPOSInventoryWriteOff.objects.select_for_update().get(pk=writeoff_id)
    if writeoff.is_voided:
        raise ValueError("This write-off has already been reversed.")
    lot = EggPOSInventoryLot.objects.select_for_update().get(pk=writeoff.lot_id)
    if lot.quantity_remaining + writeoff.quantity > lot.quantity_received:
        raise ValueError("Cannot restore more stock than the original lot received.")
    lot.quantity_remaining += writeoff.quantity
    lot.save(update_fields=["quantity_remaining"])
    writeoff.is_voided = True
    writeoff.voided_at = timezone.now()
    writeoff.voided_by = actor
    writeoff.void_reason = reason
    writeoff.save(update_fields=["is_voided", "voided_at", "voided_by", "void_reason"])
    _journal_loss(writeoff, reverse=True, user=actor)
    return writeoff


def revalue_writeoffs_for_lot(*, lot, new_unit_cost, actor):
    """Keep damaged-quantity COGS consistent if a purchase cost is corrected.

    Call inside the supplier purchase correction transaction. The purchase
    correction audit already stores actor/reason; this appends the revaluation
    details to each affected write-off as an additional audit note. Both the
    original and (if present) reversal journal are re-synced at the new amount.
    """
    updated = 0
    new_unit_cost = Decimal(new_unit_cost)
    for writeoff in EggPOSInventoryWriteOff.objects.select_for_update().filter(lot=lot):
        previous_amount = writeoff.total_cost
        new_amount = money(new_unit_cost * writeoff.quantity)
        if writeoff.unit_cost == new_unit_cost and previous_amount == new_amount:
            continue
        stamp = timezone.localtime().strftime("%d %b %Y %H:%M")
        by = actor.get_username() if actor else "system"
        writeoff.notes = (writeoff.notes or "") + (
            f"\n[Supplier cost correction {stamp} by {by}: "
            f"Rs {previous_amount:.2f} -> Rs {new_amount:.2f}]"
        )
        writeoff.unit_cost = new_unit_cost
        writeoff.total_cost = new_amount
        writeoff.save(update_fields=["unit_cost", "total_cost", "notes"])
        _journal_loss(writeoff)
        if writeoff.is_voided:
            _journal_loss(writeoff, reverse=True, user=writeoff.voided_by or actor)
        updated += 1
    return updated
