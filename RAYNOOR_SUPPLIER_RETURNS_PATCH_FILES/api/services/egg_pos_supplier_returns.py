"""Transactional FIFO supplier returns + supplier credits/refunds; no sale or cash loss."""
from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4
from django.db import transaction
from django.utils import timezone
from api.models.egg_pos import EggPOSInventoryLot
from api.models.egg_pos_supplier_returns import EggPOSSupplierReturn, EggPOSSupplierReturnRefund
from api.services.accounting import line, money, post_entry
from api.services.egg_pos_mixed_sources import packed_source_reserved, primary_id, source_ids
from api.services.online_shop import reserved_quantity


def _money_positive(value, field):
    try:
        d = money(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{field} must be a valid amount.")
    if d <= 0:
        raise ValueError(f"{field} must be positive.")
    return d


def _token(raw):
    try:
        return UUID(str(raw))
    except (ValueError, TypeError):
        raise ValueError("Invalid request token. Reload the form.")


def _date(raw, label):
    try:
        d = date.fromisoformat(str(raw))
    except (TypeError, ValueError):
        raise ValueError(f"Enter a valid {label}.")
    if d > timezone.localdate():
        raise ValueError(f"{label} cannot be in the future.")
    return d


def _assert_admin(actor):
    if not actor or not actor.is_authenticated or not actor.is_active or not (actor.is_staff or actor.is_superuser):
        raise ValueError("Only a logged-in administrator may record or correct supplier returns.")


def _journal_return(obj, reverse=False, actor=None):
    """Inventory exits at landed cost; creditor/receivable at supplier-agreed amount.

    Difference is expensed as unrecovered freight/sticker cost (or other return
    variance); over-recovery is separate other income, never inflated sales.
    """
    delta = money(obj.inventory_value - obj.supplier_credit)
    party = dict(party_type="supplier", party_id=obj.supplier_id, party_name=obj.supplier.name)
    entries = [
        line("2000" if obj.settlement == "credit_note" else "1160", debit=obj.supplier_credit,
             description="Supplier credit approved", **party),
        line("1200", credit=obj.inventory_value, description=f"{obj.quantity} returned {obj.lot.product.stock_unit_plural}"),
    ]
    if delta > 0:
        entries.append(line("6160", debit=delta, description="Unrecovered landed cost / supplier return difference"))
    elif delta < 0:
        entries.append(line("4100", credit=-delta, description="Supplier return recovery above stock value"))
    if reverse:
        entries = [
            line(row["code"], debit=row["credit"], credit=row["debit"],
                 description="Reversal: " + row.get("description", ""),
                 party_type=row.get("party_type", ""), party_id=row.get("party_id"), party_name=row.get("party_name", ""))
            for row in entries
        ]
    post_entry(
        source_key=f"egg_pos:supplier_return:{'reverse:' if reverse else ''}{obj.pk}",
        entry_date=timezone.localdate() if reverse else obj.return_date,
        reference=obj.reference + ("-REV" if reverse else ""),
        memo=("Reverse supplier return" if reverse else "Supplier purchase return") + f" #{obj.purchase_id}",
        source_type="supplier_return_reversal" if reverse else "supplier_purchase_return",
        source_id=obj.pk, created_by=actor or obj.created_by, lines=entries,
    )


def _journal_refund(refund, reverse=False, actor=None):
    party = dict(party_type="supplier", party_id=refund.supplier_return.supplier_id,
                 party_name=refund.supplier_return.supplier.name)
    entries = [
        line("1000" if refund.payment_method == "cash" else "1010", debit=refund.amount, description="Supplier refund received"),
        line("1160", credit=refund.amount, description="Supplier return refund collected", **party),
    ]
    if reverse:
        entries = [line(row["code"], debit=row["credit"], credit=row["debit"],
                        description="Reversed supplier refund", party_type=row.get("party_type", ""),
                        party_id=row.get("party_id"), party_name=row.get("party_name", "")) for row in entries]
    post_entry(source_key=f"egg_pos:supplier_refund:{'reverse:' if reverse else ''}{refund.pk}",
               entry_date=timezone.localdate() if reverse else refund.refund_date,
               reference=f"RNREF-{refund.pk:06d}" + ("-REV" if reverse else ""),
               memo=f"Supplier return {refund.supplier_return.reference} refund" + (" reversed" if reverse else ""),
               source_type="supplier_return_refund_reverse" if reverse else "supplier_return_refund",
               source_id=refund.pk, created_by=actor or refund.created_by, lines=entries)


@transaction.atomic
def record_return(*, lot_id, quantity, supplier_credit, settlement, reason,
                  actor, return_date, token, supplier_reference="", notes=""):
    _assert_admin(actor)
    token = _token(token)
    if EggPOSSupplierReturn.objects.filter(token=token).exists():
        raise ValueError("This return was already saved; duplicate submission blocked.")
    try:
        qty = int(quantity)
    except (TypeError, ValueError):
        raise ValueError("Quantity must be a whole number.")
    if qty <= 0:
        raise ValueError("Enter at least one returned egg or jar.")
    credit = _money_positive(supplier_credit, "Supplier-agreed credit")
    if settlement not in dict(EggPOSSupplierReturn.SETTLEMENT):
        raise ValueError("Choose how the supplier will settle the return.")
    if reason not in dict(EggPOSSupplierReturn.REASONS):
        raise ValueError("Choose a return reason.")
    on_date = _date(return_date, "return date")
    try:
        base_lot = EggPOSInventoryLot.objects.only("product_id").get(pk=lot_id)
    except (EggPOSInventoryLot.DoesNotExist, ValueError, TypeError):
        raise ValueError("Select a valid purchase lot.")
    group_id = primary_id(base_lot.product_id)
    # Same lot-lock strategy as sale/wastage: consistent group order.
    lots = list(EggPOSInventoryLot.objects.select_for_update().filter(
        product_id__in=source_ids(group_id), quantity_remaining__gt=0,
    ).order_by("id"))
    lot = next((row for row in lots if row.pk == int(lot_id)), None)
    if not lot or not lot.purchase_id or lot.source_type != "purchase" or not lot.supplier_id:
        raise ValueError("Only remaining purchased stock can be returned to a supplier.")
    if lot.purchase.is_voided:
        raise ValueError("The original purchase was voided.")
    if on_date < lot.received_date:
        raise ValueError("Return date cannot precede receipt of the purchased goods.")
    if qty > lot.quantity_remaining:
        raise ValueError(f"Only {lot.quantity_remaining} physical units remain in this lot.")
    today = timezone.localdate()
    eligible = lambda row: row.received_date <= today and (not row.expiry_date or row.expiry_date >= today)
    if eligible(lot):
        group_physical = sum(row.quantity_remaining for row in lots if eligible(row))
        source_physical = sum(row.quantity_remaining for row in lots if eligible(row) and row.product_id == lot.product_id)
        if group_physical - qty < reserved_quantity(group_id):
            raise ValueError("Stock is reserved for online orders; release or fulfill reservations first.")
        if source_physical - qty < packed_source_reserved(lot.product_id):
            raise ValueError("Some stock of this SKU is allocated to packed orders.")
    unit = Decimal(lot.unit_cost)
    entry = EggPOSSupplierReturn.objects.create(
        token=token, lot=lot, purchase_id=lot.purchase_id, supplier_id=lot.supplier_id,
        return_date=on_date, quantity=qty, unit_cost=unit,
        inventory_value=money(unit * qty), supplier_credit=credit,
        settlement=settlement, reason=reason, supplier_reference=(supplier_reference or "")[:120],
        notes=(notes or "")[:1000], created_by=actor,
    )
    lot.quantity_remaining -= qty
    lot.save(update_fields=["quantity_remaining"])
    _journal_return(entry)
    return entry


@transaction.atomic
def record_refund(*, return_id, amount, payment_method, refund_date, token, actor, bank_reference=""):
    _assert_admin(actor)
    token = _token(token)
    if EggPOSSupplierReturnRefund.objects.filter(token=token).exists():
        raise ValueError("This refund was already recorded.")
    obj = EggPOSSupplierReturn.objects.select_for_update().select_related("supplier").get(pk=return_id)
    if obj.is_voided or obj.settlement != "refund_due":
        raise ValueError("Only an active return awaiting a supplier refund can receive this payment.")
    if payment_method not in dict(EggPOSSupplierReturnRefund.PAYMENT):
        raise ValueError("Choose Cash or Bank as the actual refund destination.")
    amount = _money_positive(amount, "Refund amount")
    on_date = _date(refund_date, "refund date")
    if on_date < obj.return_date:
        raise ValueError("Refund date cannot be before the return date.")
    if amount > obj.refund_remaining:
        raise ValueError(f"Supplier refund remaining is only Rs {obj.refund_remaining:,.2f}.")
    obj_refund = EggPOSSupplierReturnRefund.objects.create(
        token=token, supplier_return=obj, refund_date=on_date, amount=amount,
        payment_method=payment_method, bank_reference=(bank_reference or "")[:120], created_by=actor,
    )
    _journal_refund(obj_refund)
    return obj_refund


@transaction.atomic
def reverse_refund(*, refund_id, actor, reason):
    _assert_admin(actor)
    reason = (reason or "").strip()
    if len(reason) < 10:
        raise ValueError("Enter a detailed refund reversal reason (10+ characters).")
    refund = EggPOSSupplierReturnRefund.objects.select_for_update().select_related("supplier_return", "supplier_return__supplier").get(pk=refund_id)
    EggPOSSupplierReturn.objects.select_for_update().get(pk=refund.supplier_return_id)
    if refund.is_voided or refund.supplier_return.is_voided:
        raise ValueError("Refund/return already reversed.")
    refund.is_voided = True
    refund.voided_at = timezone.now()
    refund.voided_by = actor
    refund.void_reason = reason[:500]
    refund.save(update_fields=["is_voided", "voided_at", "voided_by", "void_reason"])
    _journal_refund(refund, reverse=True, actor=actor)
    return refund


@transaction.atomic
def reverse_return(*, return_id, actor, reason):
    _assert_admin(actor)
    reason = (reason or "").strip()
    if len(reason) < 10:
        raise ValueError("Enter a detailed return reversal reason (10+ characters).")
    obj = EggPOSSupplierReturn.objects.select_for_update().select_related("supplier", "lot", "lot__product").get(pk=return_id)
    if obj.is_voided:
        raise ValueError("Return already reversed.")
    if obj.refunds.filter(is_voided=False).exists():
        raise ValueError("Reverse all received refunds first, then reverse this return.")
    lot = EggPOSInventoryLot.objects.select_for_update().get(pk=obj.lot_id)
    if lot.quantity_remaining + obj.quantity > lot.quantity_received:
        raise ValueError("Cannot restore quantity above originally received stock.")
    lot.quantity_remaining += obj.quantity
    lot.save(update_fields=["quantity_remaining"])
    obj.is_voided = True
    obj.voided_at = timezone.now()
    obj.voided_by = actor
    obj.void_reason = reason[:500]
    obj.save(update_fields=["is_voided", "voided_at", "voided_by", "void_reason"])
    _journal_return(obj, reverse=True, actor=actor)
    return obj
