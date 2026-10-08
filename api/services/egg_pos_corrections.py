"""Audited, atomic Egg POS invoice corrections. No payment rows are rewritten."""
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from api.models.egg_pos import (
    EggPOSInventoryLot, EggPOSProduct, EggPOSSale, EggPOSSaleItem,
    EggPOSSaleAllocation, EggPOSSaleAudit,
)
from api.models.accounting import JournalEntry
from api.services.egg_pos import money, reverse_sale
from api.services.egg_pos_accounting import sync_sale_invoice, sync_sale_payment

UNITS = {"egg": 1, "dozen": 12, "tray": 30, "crate": 360}


def _snapshot(sale):
    return {
        "reversed": sale.is_reversed,
        "subtotal": str(sale.subtotal), "discount": str(sale.discount_amount),
        "net_total": str(sale.net_total), "cogs": str(sale.cogs_total),
        "profit": str(sale.profit_total),
        "payments": [{"id": p.pk, "amount": str(p.amount), "method": p.payment_method}
                     for p in sale.payments.order_by("id")],
        "items": [
            {"id": item.pk, "product_id": item.product_id, "quantity": item.quantity,
             "sale_unit": item.sale_unit, "unit_count": item.unit_count,
             "unit_rate": str(item.unit_rate), "line_total": str(item.line_total),
             "cogs": str(item.cogs_amount),
             "lots": [{"lot_id": a.lot_id, "qty": a.quantity, "cogs": str(a.cogs_amount)}
                      for a in item.lot_allocations.order_by("id")]}
            for item in sale.items.order_by("id")
        ],
    }


def _audit(sale, actor, action, reason, before, after):
    EggPOSSaleAudit.objects.create(sale=sale, admin=actor, action=action,
                                   reason=reason[:255], before=before, after=after)


def _unlock_allocated(item, qty):
    """Return eggs from the latest allocations first; keep earlier FIFO costs unchanged."""
    available_alloc = sum(a.quantity for a in item.lot_allocations.all())
    if qty > available_alloc:
        raise ValueError("Invoice allocation records are incomplete. Stop and reconcile this invoice first.")
    remaining = qty
    for allocation in item.lot_allocations.select_related("lot").order_by("-lot__received_date", "-lot_id", "-id"):
        if remaining == 0:
            break
        take = min(remaining, allocation.quantity)
        lot = EggPOSInventoryLot.objects.select_for_update().get(pk=allocation.lot_id)
        if lot.quantity_remaining + take > lot.quantity_received:
            raise ValueError(f"FIFO lot {lot.lot_code} would exceed its received eggs; check prior stock corrections.")
        lot.quantity_remaining += take
        lot.save(update_fields=["quantity_remaining"])
        allocation.quantity -= take
        if allocation.quantity:
            allocation.cogs_amount = money(Decimal(allocation.quantity) * allocation.unit_cost)
            allocation.save(update_fields=["quantity", "cogs_amount"])
        else:
            allocation.delete()
        remaining -= take


def _allocate_extra(item, qty, sale_date):
    if qty <= 0:
        return
    lots = list(
        EggPOSInventoryLot.objects.select_for_update()
        .filter(product_id=item.product_id, quantity_remaining__gt=0, received_date__lte=sale_date)
        .filter(Q(expiry_date__isnull=True) | Q(expiry_date__gte=sale_date))
        .order_by("received_date", "id")
    )
    if sum(lot.quantity_remaining for lot in lots) < qty:
        raise ValueError(f"Insufficient FIFO stock for {item.product.name} on the invoice date.")
    remaining = qty
    for lot in lots:
        if not remaining:
            break
        take = min(remaining, lot.quantity_remaining)
        lot.quantity_remaining -= take
        lot.save(update_fields=["quantity_remaining"])
        EggPOSSaleAllocation.objects.create(
            sale_item=item, lot=lot, quantity=take, unit_cost=lot.unit_cost,
            cogs_amount=money(Decimal(take) * lot.unit_cost),
        )
        remaining -= take


@transaction.atomic
def edit_invoice(sale_id, actor, posted_rows, discount, reason):
    sale = EggPOSSale.objects.select_for_update().get(pk=sale_id)
    if sale.is_reversed:
        raise ValueError("Restore this reversed invoice before editing it.")
    if not reason.strip():
        raise ValueError("A reason is required for editing an invoice.")
    if not posted_rows or len(posted_rows) > 40:
        raise ValueError("An invoice requires 1 to 40 valid item rows.")

    original_items = {obj.pk: obj for obj in sale.items.select_related("product").order_by("id")}
    original_ids = set(original_items)
    used_ids = set()
    clean_rows = []
    for row in posted_rows:
        raw_id = row.get("item_id") or ""
        item_id = int(raw_id) if raw_id else None
        if item_id is not None:
            if item_id not in original_ids or item_id in used_ids:
                raise ValueError("Invalid or duplicated invoice item identifier.")
            used_ids.add(item_id)
        unit = (row.get("sale_unit") or "").strip()
        if unit not in UNITS:
            raise ValueError("Choose a valid selling unit.")
        try:
            count = int(row.get("unit_count", ""))
        except (ValueError, TypeError):
            raise ValueError("Each quantity must be a whole number.")
        if count < 0 or count > 100000:
            raise ValueError("Quantity is outside the allowed range.")
        if count == 0:
            if item_id is None:
                continue
            clean_rows.append((item_id, None, unit, 0, Decimal("0")))
            continue
        try:
            product_id = int(row.get("product_id", ""))
            rate = money(Decimal(row.get("unit_rate", "")))
        except (TypeError, ValueError, ArithmeticError):
            raise ValueError("Choose a product and enter a valid rate.")
        if rate <= 0 or rate > Decimal("9999999999.99"):
            raise ValueError("Each selling rate must be greater than zero.")
        product = EggPOSProduct.objects.filter(pk=product_id, is_active=True).first()
        if product is None:
            raise ValueError("The selected egg product is inactive or missing.")
        clean_rows.append((item_id, product, unit, count, rate))
    if set(original_ids) != used_ids:
        raise ValueError("All original invoice items must be included (use quantity 0 to remove a row).")
    active = [row for row in clean_rows if row[3] > 0]
    if not active:
        raise ValueError("An invoice needs at least one item with a positive quantity.")
    try:
        discount = money(Decimal(discount))
    except (TypeError, ValueError, ArithmeticError):
        raise ValueError("Discount is invalid.")
    subtotal = money(sum((Decimal(count) * rate for _, _, _, count, rate in active), Decimal("0")))
    if discount < 0 or discount >= subtotal:
        raise ValueError("Discount must be zero or less than the invoice subtotal.")
    net_total = money(subtotal - discount)
    paid = money(sum((Decimal(p.amount) for p in sale.payments.all()), Decimal("0")))
    if paid > net_total:
        raise ValueError(
            f"Payments already received (Rs {paid:,.2f}) exceed the edited invoice "
            f"(Rs {net_total:,.2f}). Resolve the excess payment separately before editing."
        )

    before = _snapshot(sale)
    cogs_total = Decimal("0")
    for item_id, product, unit, count, rate in clean_rows:
        item = original_items.get(item_id)
        qty = count * UNITS[unit]
        if item:
            old_qty = int(item.quantity)
            if count == 0:
                _unlock_allocated(item, old_qty)
                item.delete()
                continue
            if item.product_id != product.pk:
                _unlock_allocated(item, old_qty)
                item.product = product
                item.product_id = product.pk
                _allocate_extra(item, qty, sale.sale_date)
            elif qty < old_qty:
                _unlock_allocated(item, old_qty - qty)
            elif qty > old_qty:
                _allocate_extra(item, qty - old_qty, sale.sale_date)
        else:
            item = EggPOSSaleItem(sale=sale, product=product)
            # New rows must exist before FIFO allocations can be inserted.
            item.quantity = qty
            item.sale_unit = unit
            item.unit_count = count
            item.unit_size = UNITS[unit]
            item.unit_rate = rate
            item.line_total = money(Decimal(count) * rate)
            item.unit_price = money(item.line_total / Decimal(qty))
            item.save()
            _allocate_extra(item, qty, sale.sale_date)
        item.quantity = qty
        item.sale_unit = unit
        item.unit_count = count
        item.unit_size = UNITS[unit]
        item.unit_rate = rate
        item.line_total = money(Decimal(count) * rate)
        item.unit_price = money(item.line_total / Decimal(qty))
        item.cogs_amount = money(sum((a.cogs_amount for a in item.lot_allocations.all()), Decimal("0")))
        item.save()
        cogs_total += item.cogs_amount

    sale.subtotal = subtotal
    sale.discount_amount = discount
    sale.net_total = net_total
    sale.cogs_total = money(cogs_total)
    sale.profit_total = money(net_total - sale.cogs_total)
    sale.save(update_fields=["subtotal", "discount_amount", "net_total", "cogs_total", "profit_total"])
    sync_sale_invoice(sale)
    _audit(sale, actor, "edit", reason, before, _snapshot(sale))
    return sale


@transaction.atomic
def reverse_invoice(sale_id, actor, reason):
    sale = EggPOSSale.objects.select_for_update().get(pk=sale_id)
    if sale.is_reversed:
        raise ValueError("This invoice is already reversed.")
    for item in sale.items.all():
        if sum(a.quantity for a in item.lot_allocations.all()) != item.quantity:
            raise ValueError("FIFO allocation is incomplete; do not reverse until stock is reconciled.")
    lot_needs = {}
    for item in sale.items.all():
        for a in item.lot_allocations.all():
            lot_needs[a.lot_id] = lot_needs.get(a.lot_id, 0) + a.quantity
    for lot in EggPOSInventoryLot.objects.select_for_update().filter(pk__in=lot_needs):
        if lot.quantity_remaining + lot_needs[lot.pk] > lot.quantity_received:
            raise ValueError(f"Lot {lot.lot_code} cannot accept the returned eggs; reconcile stock first.")
    before = _snapshot(sale)
    reverse_sale(sale, actor, reason)
    sale.refresh_from_db()
    _audit(sale, actor, "reverse", reason, before, _snapshot(sale))
    return sale


@transaction.atomic
def restore_invoice(sale_id, actor, reason):
    """Undo a mistaken reversal, using the ORIGINAL lot allocations and payments."""
    sale = EggPOSSale.objects.select_for_update().get(pk=sale_id)
    if not sale.is_reversed:
        raise ValueError("Only a reversed invoice can be restored.")
    if not reason.strip():
        raise ValueError("An undo-reversal reason is required.")
    before = _snapshot(sale)
    # Reversal retains allocation rows. Restoring needs those very same lots.
    needs = {}
    for item in sale.items.all():
        allocations = list(item.lot_allocations.all())
        if sum(a.quantity for a in allocations) != item.quantity:
            raise ValueError("Original FIFO allocation records are incomplete; manual review required.")
        for alloc in allocations:
            needs[alloc.lot_id] = needs.get(alloc.lot_id, 0) + alloc.quantity
    lots = {
        lot.pk: lot for lot in EggPOSInventoryLot.objects.select_for_update()
        .filter(pk__in=needs).order_by("pk")
    }
    for lot_id, qty in needs.items():
        lot = lots.get(lot_id)
        if lot is None or lot.quantity_remaining < qty:
            raise ValueError(
                "Cannot restore: original FIFO eggs have since been sold or stock is inconsistent. "
                "Review the lot before retrying; no records were changed."
            )
    for lot_id, qty in needs.items():
        lot = lots[lot_id]
        lot.quantity_remaining -= qty
        lot.save(update_fields=["quantity_remaining"])
    sale.is_reversed = False
    sale.reversed_at = None
    sale.reversed_by = None
    sale.reversal_reason = ""
    sale.save(update_fields=["is_reversed", "reversed_at", "reversed_by", "reversal_reason"])
    sync_sale_invoice(sale)
    for payment in sale.payments.all():
        sync_sale_payment(payment)
    _audit(sale, actor, "restore", reason, before, _snapshot(sale))
    return sale
