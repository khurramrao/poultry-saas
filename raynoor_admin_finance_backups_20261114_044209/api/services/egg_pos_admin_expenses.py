"""Immutable, balanced accounting for admin costs and their audited reversals."""
from api.models.egg_pos_admin_expense import EggPOSAdminExpense
from django.utils import timezone
from api.services.accounting import line, post_entry

CATEGORY_ACCOUNT = {
    "rent": "6300",
    "utilities": "6310",
    "internet": "6320",
    "stationery": "6330",
    "supplies": "6340",
    "repair": "6350",
    "marketing": "6360",
    "delivery": "6110",  # Outbound delivery paid by RayNoor; selling expense (never stock cost).
    "professional": "6370",
    "other": "6380",
    "equipment": "1500",  # Capital asset, not current-period expense.
}
PAYMENT_ACCOUNT = {"cash": "1000", "bank": "1010"}


def sync_admin_expense(expense):
    debit = CATEGORY_ACCOUNT[expense.category]
    credit = PAYMENT_ACCOUNT[expense.payment_source]
    return post_entry(
        source_key=f"egg_pos:admin_expense:{expense.pk}",
        entry_date=expense.expense_date,
        reference=expense.reference or f"AEXP-{expense.pk:06d}",
        memo=f"{expense.get_category_display()} – {expense.paid_to or 'Office / Administration'}",
        source_type="egg_pos_admin_expense",
        source_id=expense.pk,
        created_by=expense.recorded_by,
        lines=[
            line(debit, debit=expense.amount, description=expense.get_category_display()),
            line(credit, credit=expense.amount, description=expense.get_payment_source_display()),
        ],
    )


def sync_admin_expense_void(expense):
    # Never erase original journals: retain both entry and separate reversal in audit history.
    if not expense.is_voided:
        return None
    debit = PAYMENT_ACCOUNT[expense.payment_source]
    credit = CATEGORY_ACCOUNT[expense.category]
    return post_entry(
        source_key=f"egg_pos:admin_expense_void:{expense.pk}",
        entry_date=timezone.localdate(expense.voided_at),
        reference=f"VOID-AEXP-{expense.pk:06d}",
        memo=f"Void admin expense #{expense.pk}: {expense.void_reason}"[:255],
        source_type="egg_pos_admin_expense_void",
        source_id=expense.pk,
        created_by=expense.voided_by,
        lines=[
            line(debit, debit=expense.amount, description="Reverse admin payment"),
            line(credit, credit=expense.amount, description="Reverse admin expense / asset"),
        ],
    )


def rebuild_admin_expenses():
    """Idempotent rebuild hook invoked by the existing Egg POS journal rebuild."""
    for expense in EggPOSAdminExpense.objects.select_related("recorded_by", "voided_by").order_by("pk"):
        sync_admin_expense(expense)
        if expense.is_voided:
            sync_admin_expense_void(expense)
