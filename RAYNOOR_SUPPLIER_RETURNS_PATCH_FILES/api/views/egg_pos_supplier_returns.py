"""Supplier return/credit/refund UI; separate from damage and sale workflows."""
from decimal import Decimal
from uuid import uuid4
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import F
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from api.models.egg_pos import EggPOSInventoryLot
from api.models.egg_pos_supplier_returns import EggPOSSupplierReturn, EggPOSSupplierReturnRefund
from api.services.egg_pos_supplier_returns import (
    record_return, record_refund, reverse_return, reverse_refund,
)


def _admin(user):
    return bool(user.is_authenticated and user.is_active and (user.is_superuser or user.is_staff))


@login_required
@require_http_methods(["GET", "POST"])
def purchase_returns(request):
    if not _admin(request.user):
        messages.error(request, "Only POS administrators may manage supplier purchase returns.")
        return redirect("egg_pos_dashboard")

    if request.method == "POST":
        # Admin re-authentication is required for all inventory, payables and
        # bank/cash changes. Failure must not mutate anything.
        if not request.user.check_password(request.POST.get("admin_password", "")):
            messages.error(request, "Incorrect administrator password; no changes were made.")
            return redirect("egg_pos_purchase_returns")
        action = (request.POST.get("action") or "").strip()
        try:
            if action == "return":
                obj = record_return(
                    lot_id=request.POST.get("lot_id"), quantity=request.POST.get("quantity"),
                    supplier_credit=request.POST.get("supplier_credit"),
                    settlement=request.POST.get("settlement"), reason=request.POST.get("reason"),
                    return_date=request.POST.get("return_date"),
                    supplier_reference=request.POST.get("supplier_reference"),
                    notes=request.POST.get("notes"), actor=request.user,
                    token=request.POST.get("token"),
                )
                messages.success(request,
                    f"{obj.reference}: {obj.quantity} units removed; "
                    f"supplier credit/refund claim Rs {obj.supplier_credit:,.2f} recorded."
                )
            elif action == "refund":
                obj = record_refund(
                    return_id=request.POST.get("return_id"), amount=request.POST.get("amount"),
                    refund_date=request.POST.get("refund_date"), payment_method=request.POST.get("payment_method"),
                    bank_reference=request.POST.get("bank_reference"),
                    token=request.POST.get("token"), actor=request.user,
                )
                messages.success(request, f"Supplier refund Rs {obj.amount:,.2f} recorded once.")
            elif action == "reverse_refund":
                obj = reverse_refund(refund_id=request.POST.get("refund_id"), actor=request.user,
                                     reason=request.POST.get("reason"))
                messages.success(request, f"Refund #{obj.pk} reversed with audit history.")
            elif action == "reverse_return":
                obj = reverse_return(return_id=request.POST.get("return_id"), actor=request.user,
                                     reason=request.POST.get("reason"))
                messages.success(request, f"{obj.reference} reversed; stock restored.")
            else:
                raise ValueError("Unknown supplier return action.")
        except (ValueError, EggPOSSupplierReturn.DoesNotExist, EggPOSSupplierReturnRefund.DoesNotExist) as exc:
            messages.error(request, str(exc))
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Supplier return processing failed")
            messages.error(request, "Unable to complete the supplier return. No partial changes were saved.")
        return redirect("egg_pos_purchase_returns")

    lots = list(EggPOSInventoryLot.objects.filter(
        source_type="purchase", purchase__is_voided=False, quantity_remaining__gt=0,
    ).select_related("product", "supplier", "purchase").order_by("product__name", "received_date", "id")[:300])
    for lot in lots:
        purchase_item = getattr(lot, "purchase_item", None)
        lot.supplier_unit_price = purchase_item.unit_cost if purchase_item is not None else lot.unit_cost
        lot.display_cost = f"{Decimal(lot.supplier_unit_price):.4f}"
    history = list(EggPOSSupplierReturn.objects.select_related(
        "supplier", "purchase", "lot__product", "created_by"
    ).prefetch_related("refunds")[:100])
    for obj in history:
        obj.refund_token = uuid4()
        obj.active_refunds = [p for p in obj.refunds.all() if not p.is_voided]
    selected = request.GET.get("lot", "")
    selected = str(selected) if any(str(row.pk) == str(selected) for row in lots) else ""
    return render(request, "api/egg_pos_purchase_returns.html", {
        "lots": lots, "history": history, "selected_lot": selected,
        "today": timezone.localdate(), "token": uuid4(),
        "settlement_choices": EggPOSSupplierReturn.SETTLEMENT,
        "reason_choices": EggPOSSupplierReturn.REASONS,
        "refund_methods": EggPOSSupplierReturnRefund.PAYMENT,
    })
