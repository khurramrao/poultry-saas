from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from api.models.egg_pos import (
    EggPOSFarmTransfer,
    EggPOSFarmTransferPayment,
    EggPOSFarmTransferItem,
    EggPOSInventoryLot,
    EggPOSProduct,
    EggPOSCustomer,
    EggPOSPurchase,
    EggPOSPurchaseItem,
    EggPOSSale,
    EggPOSSalePayment,
    EggPOSSaleItem,
    EggPOSSupplier,
    EggPOSUserAccess,
    EggPOSSupplierPayment,
    EggPOSExpense,
    EggPOSCashSettlement,
    EggPOSCashHandoverRequest,
    EggPOSCommissionPeriod,
    EggPOSCommissionPayment,
    EggPOSOwnerCapitalTransaction,
)
from api.models.sensor import Batch
from api.models.accounting import ChartOfAccount, JournalEntry, JournalLine
from api.services.egg_pos import (
    ZERO,
    allocate_fifo_to_sale_item,
    available_product_stock,
    can_manage,
    can_sell,
    farm_egg_stock,
    is_admin,
    make_lot_code,
    money,
    reverse_sale,
)
from api.services.accounting import (
    account_activity,
    customer_balance as gl_customer_balance,
    ensure_default_accounts,
    income_statement as build_income_statement,
    normal_account_balance,
    party_activity,
    staff_cash_balance,
    staff_commission_balance,
    staff_reimbursement_balance,
    supplier_balance as gl_supplier_balance,
    trial_balance as build_trial_balance,
)
from api.services.egg_pos_accounting import (
    commission_preview,
    sync_cash_settlement,
    sync_commission_payment,
    sync_commission_period,
    sync_expense,
    sync_farm_transfer,
    sync_farm_transfer_payment,
    sync_purchase,
    sync_sale_invoice,
    sync_sale_payment,
    sync_supplier_payment,
    sync_owner_capital,
)


def _deny(request, message="You do not have Egg POS access."):
    messages.error(request, message)
    return redirect("dashboard")


def _parse_positive_int(value, label):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a whole number.")
    if number <= 0:
        raise ValueError(f"{label} must be greater than zero.")
    return number


def _parse_money(value, label, allow_zero=False):
    try:
        amount = money(Decimal(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{label} is invalid.")
    if amount < ZERO or (not allow_zero and amount <= ZERO):
        rule = "zero or greater" if allow_zero else "greater than zero"
        raise ValueError(f"{label} must be {rule}.")
    return amount


UNIT_COST_QUANT = Decimal("0.000001")


def _parse_unit_cost(value, label):
    try:
        amount = Decimal(value).quantize(UNIT_COST_QUANT)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{label} is invalid.")
    if amount <= ZERO:
        raise ValueError(f"{label} must be greater than zero.")
    return amount


def _parse_date(value, label, default=None):
    if not value:
        return default or timezone.localdate()
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{label} is invalid.")


def _stock_rows(products):
    rows = []
    today = timezone.localdate()
    for product in products:
        lots = list(
            EggPOSInventoryLot.objects.filter(product=product, quantity_remaining__gt=0)
            .select_related("supplier", "farm_batch", "purchase", "farm_transfer")
            .order_by("received_date", "id")
        )
        sellable_lots = [
            lot for lot in lots
            if lot.received_date <= today
            and (not lot.expiry_date or lot.expiry_date >= today)
        ]
        expired_lots = [
            lot for lot in lots
            if lot.expiry_date and lot.expiry_date < today
        ]
        current = sum(int(lot.quantity_remaining or 0) for lot in sellable_lots)
        expired = sum(int(lot.quantity_remaining or 0) for lot in expired_lots)
        purchased = sum(
            int(lot.quantity_remaining or 0)
            for lot in sellable_lots
            if lot.source_type == "purchase"
        )
        farm = sum(
            int(lot.quantity_remaining or 0)
            for lot in sellable_lots
            if lot.source_type == "farm"
        )
        value = sum((lot.stock_value for lot in sellable_lots), ZERO)

        # Dashboard stock bars must represent how much of the currently-open
        # inventory lots is still remaining.  Using the raw egg count as a CSS
        # percentage (e.g. 270 -> 270%) makes almost every bar look full.
        # We therefore compare remaining eggs with the original quantity of the
        # sellable lots that are still open. Fully depleted lots are already
        # excluded from ``lots`` by the queryset above.
        stock_reference = sum(int(lot.quantity_received or 0) for lot in sellable_lots)
        if stock_reference > 0:
            stock_percent = min(100, max(0, round((current / stock_reference) * 100)))
        else:
            stock_percent = 0

        rows.append({
            "product": product,
            "stock": current,
            "stock_reference": stock_reference,
            "stock_percent": stock_percent,
            "expired_stock": expired,
            "purchase_stock": purchased,
            "farm_stock": farm,
            "stock_value": money(value),
            "low_stock": current <= int(product.low_stock_eggs or 0),
            # Keep both key names because the admin inventory and the
            # salesperson mobile dashboard use these practical-unit values.
            "crates": current // 360,
            "trays": current // 30,
            "dozens": current // 12,
            "max_crates": current // 360,
            "max_trays": current // 30,
            "max_dozens": current // 12,
            "lots": lots,
        })
    return rows


def _customer_sales_totals(sales):
    sales = list(sales)
    invoiced = money(sum((Decimal(sale.net_total or 0) for sale in sales), ZERO))
    paid = money(sum((Decimal(sale.amount_paid or 0) for sale in sales), ZERO))
    outstanding = money(sum((Decimal(sale.balance_due or 0) for sale in sales), ZERO))
    return {
        "invoice_count": len(sales),
        "invoiced": invoiced,
        "paid": paid,
        "outstanding": outstanding,
    }


def _customer_row(customer, user, manage):
    all_sales = list(customer.sales.filter(is_reversed=False))
    overall = _customer_sales_totals(all_sales)

    if manage:
        visible_sales = all_sales
    else:
        visible_sales = [
            sale
            for sale in all_sales
            if sale.created_by_id == user.id
        ]

    visible = _customer_sales_totals(visible_sales)

    last_sale_date = None
    if visible_sales:
        last_sale_date = max(sale.sale_date for sale in visible_sales)

    return {
        "customer": customer,
        "overall": overall,
        "visible": visible,
        "last_sale_date": last_sale_date,
    }


@login_required
def dashboard(request):
    if not can_sell(request.user):
        return _deny(request)

    today = timezone.localdate()
    products = list(EggPOSProduct.objects.filter(is_active=True).order_by("name"))
    stock_rows = _stock_rows(products)

    sales_qs = EggPOSSale.objects.filter(sale_date=today, is_reversed=False)
    if not can_manage(request.user):
        sales_qs = sales_qs.filter(created_by=request.user)

    today_sales_count = sales_qs.count()
    today_revenue = money(sales_qs.aggregate(total=Sum("net_total"))["total"] or ZERO)
    today_profit = money(sales_qs.aggregate(total=Sum("profit_total"))["total"] or ZERO)
    today_eggs_sold = int(
        EggPOSSaleItem.objects.filter(sale__in=sales_qs)
        .aggregate(total=Sum("quantity"))["total"] or 0
    )

    month_start = today.replace(day=1)
    month_sales_qs = EggPOSSale.objects.filter(
        is_reversed=False,
        sale_date__gte=month_start,
        sale_date__lte=today,
    )
    if not can_manage(request.user):
        month_sales_qs = month_sales_qs.filter(created_by=request.user)

    month_revenue = money(
        month_sales_qs.aggregate(total=Sum("net_total"))["total"] or ZERO
    )
    month_eggs_sold = int(
        EggPOSSaleItem.objects.filter(sale__in=month_sales_qs)
        .aggregate(total=Sum("quantity"))["total"] or 0
    )

    total_stock = sum(row["stock"] for row in stock_rows)
    stock_value = money(sum((row["stock_value"] for row in stock_rows), ZERO))
    low_stock_count = sum(1 for row in stock_rows if row["low_stock"])
    active_product_count = len(products)
    farm_stock_total = sum(row["farm_stock"] for row in stock_rows)
    purchase_stock_total = sum(row["purchase_stock"] for row in stock_rows)

    recent_sales = EggPOSSale.objects.filter(is_reversed=False).select_related("created_by")
    if not can_manage(request.user):
        recent_sales = recent_sales.filter(created_by=request.user)
    recent_sales = recent_sales.order_by("-sale_date", "-id")[:10]

    manage = can_manage(request.user)
    salesperson_cash_balance = money(staff_cash_balance(request.user)) if not manage else money(normal_account_balance("1040", as_of=today))
    pending_expense_count = EggPOSExpense.objects.filter(status="pending").count() if manage else EggPOSExpense.objects.filter(status="pending", used_by=request.user).count()
    pending_handover = ZERO
    salesperson_cash_to_submit = salesperson_cash_balance
    commission_summary = {"month_earned": ZERO, "month_received": ZERO, "balance_due": ZERO}
    week_commission_preview = None
    if not manage:
        pending_handover = _pending_handover_total(request.user)
        salesperson_cash_to_submit = money(max(Decimal(salesperson_cash_balance) - Decimal(pending_handover), ZERO))
        commission_summary = _commission_summary(request.user, today)
        week_start = today - timedelta(days=today.weekday())
        week_commission_preview = commission_preview(request.user, week_start, today)

    farm_transfer_payable = ZERO
    farm_transfer_total = ZERO
    farm_transfer_paid = ZERO
    if manage:
        farm_transfers = list(
            EggPOSFarmTransfer.objects
            .prefetch_related("items", "payments")
            .order_by("-transfer_date", "-id")
        )
        farm_transfer_total = money(sum((Decimal(t.total_amount or 0) for t in farm_transfers), ZERO))
        farm_transfer_paid = money(sum((Decimal(t.amount_paid or 0) for t in farm_transfers), ZERO))
        farm_transfer_payable = money(sum((Decimal(t.balance_due or 0) for t in farm_transfers), ZERO))

    return render(request, "api/egg_pos_dashboard.html", {
        "stock_rows": stock_rows,
        "today_sales_count": today_sales_count,
        "today_revenue": today_revenue,
        "today_profit": today_profit,
        "today_eggs_sold": today_eggs_sold,
        "month_revenue": month_revenue,
        "month_eggs_sold": month_eggs_sold,
        "total_stock": total_stock,
        "stock_value": stock_value,
        "low_stock_count": low_stock_count,
        "active_product_count": active_product_count,
        "farm_stock_total": farm_stock_total,
        "purchase_stock_total": purchase_stock_total,
        "recent_sales": recent_sales,
        "farm_transfer_total": farm_transfer_total,
        "farm_transfer_paid": farm_transfer_paid,
        "farm_transfer_payable": farm_transfer_payable,
        "can_manage_pos": manage,
        "salesperson_cash_balance": salesperson_cash_balance,
        "salesperson_cash_to_submit": salesperson_cash_to_submit,
        "pending_handover": pending_handover,
        "pending_expense_count": pending_expense_count,
        "commission_summary": commission_summary,
        "week_commission_preview": week_commission_preview,
    })


@login_required
def inventory(request):
    if not can_sell(request.user):
        return _deny(request)
    products = EggPOSProduct.objects.filter(is_active=True).order_by("name")
    stock_rows = _stock_rows(products)
    manage = can_manage(request.user)
    return render(request, "api/egg_pos_inventory.html", {
        "stock_rows": stock_rows,
        "can_manage_pos": manage,
        "total_stock": sum(row["stock"] for row in stock_rows),
        "total_stock_value": money(sum((row["stock_value"] for row in stock_rows), ZERO)),
        "low_stock_count": sum(1 for row in stock_rows if row["low_stock"]),
        "expired_stock_total": sum(row["expired_stock"] for row in stock_rows),
        "products_in_stock": sum(1 for row in stock_rows if row["stock"] > 0),
    })


@login_required
@require_http_methods(["GET", "POST"])
def products(request):
    if not can_manage(request.user):
        return _deny(request, "Only Egg POS management can manage products.")

    if request.method == "POST":
        try:
            name = (request.POST.get("name") or "").strip()
            sku = (request.POST.get("sku") or "").strip().upper()
            if not name or not sku:
                raise ValueError("Product name and SKU are required.")
            weight_raw = (request.POST.get("weight_grams") or "").strip()
            weight = Decimal(weight_raw) if weight_raw else None
            price = _parse_money(request.POST.get("default_sale_price") or "0", "Default sale price", allow_zero=True)
            low_stock = int(request.POST.get("low_stock_eggs") or 100)
            if low_stock < 0:
                raise ValueError("Low stock level cannot be negative.")
            EggPOSProduct.objects.create(
                name=name,
                sku=sku,
                weight_grams=weight,
                default_sale_price=price,
                low_stock_eggs=low_stock,
                notes=(request.POST.get("notes") or "").strip(),
            )
            messages.success(request, f"{name} added to Egg POS products.")
            return redirect("egg_pos_products")
        except Exception as error:
            messages.error(request, str(error))

    return render(request, "api/egg_pos_products.html", {
        "products": EggPOSProduct.objects.all().order_by("name"),
    })


@login_required
@require_http_methods(["GET", "POST"])
def suppliers(request):
    if not can_manage(request.user):
        return _deny(request, "Only Egg POS management can manage suppliers.")

    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        if not name:
            messages.error(request, "Supplier name is required.")
        else:
            try:
                EggPOSSupplier.objects.create(
                    name=name,
                    phone=(request.POST.get("phone") or "").strip(),
                    address=(request.POST.get("address") or "").strip(),
                    notes=(request.POST.get("notes") or "").strip(),
                )
                messages.success(request, f"Supplier {name} added.")
                return redirect("egg_pos_suppliers")
            except Exception as error:
                messages.error(request, str(error))

    return render(request, "api/egg_pos_suppliers.html", {
        "suppliers": EggPOSSupplier.objects.all().order_by("name"),
    })


@login_required
def purchase_list(request):
    if not can_manage(request.user):
        return _deny(request, "Only Egg POS management can view purchases.")
    purchases = list(
        EggPOSPurchase.objects
        .select_related("supplier", "created_by")
        .prefetch_related("items__product", "items__lot", "payments")[:100]
    )
    for purchase in purchases:
        items = list(purchase.items.all())
        purchase.total_eggs = sum(int(item.quantity or 0) for item in items)
        purchase.remaining_eggs = sum(
            int(item.lot.quantity_remaining or 0)
            for item in items if item.lot_id and item.lot
        )
        purchase.sold_eggs = max(purchase.total_eggs - purchase.remaining_eggs, 0)
    return render(request, "api/egg_pos_purchases.html", {
        "purchases": purchases,
    })


@login_required
@require_http_methods(["GET", "POST"])
def add_purchase(request):
    if not can_manage(request.user):
        return _deny(request, "Only Egg POS management can add purchases.")

    products_qs = EggPOSProduct.objects.filter(is_active=True).order_by("name")
    suppliers_qs = EggPOSSupplier.objects.filter(is_active=True).order_by("name")

    if request.method == "POST":
        try:
            supplier = get_object_or_404(EggPOSSupplier, pk=request.POST.get("supplier"), is_active=True)
            purchase_date = _parse_date(request.POST.get("purchase_date"), "Purchase date")
            transport_cost = _parse_money(request.POST.get("transport_cost") or "0", "Transport cost", allow_zero=True)
            transport_method = (request.POST.get("transport_payment_method") or "cash").strip()
            valid_transport_methods = {value for value, _ in EggPOSPurchase.TRANSPORT_PAYMENT_CHOICES}
            if transport_method not in valid_transport_methods:
                raise ValueError("Select a valid transport payment source.")

            product_ids = request.POST.getlist("product_id")
            quantities = request.POST.getlist("quantity")
            costs = request.POST.getlist("unit_cost")
            line_totals = request.POST.getlist("line_total_cost")
            expiries = request.POST.getlist("expiry_date")

            rows = []
            total_qty = 0
            for index, product_id in enumerate(product_ids):
                if not product_id:
                    continue
                product = get_object_or_404(EggPOSProduct, pk=product_id, is_active=True)
                qty = _parse_positive_int(quantities[index] if index < len(quantities) else None, "Quantity")
                unit_raw = (costs[index] if index < len(costs) else "") or ""
                total_raw = (line_totals[index] if index < len(line_totals) else "") or ""
                if str(total_raw).strip():
                    line_cost = _parse_money(total_raw, "Line purchase cost")
                    unit_cost = (Decimal(line_cost) / Decimal(qty)).quantize(UNIT_COST_QUANT)
                else:
                    unit_cost = _parse_unit_cost(unit_raw, "Unit cost")
                expiry = _parse_date(expiries[index], "Expiry date", default=None) if index < len(expiries) and expiries[index] else None
                if expiry and expiry < purchase_date:
                    raise ValueError("Expiry date cannot be before purchase date.")
                rows.append((product, qty, unit_cost, expiry))
                total_qty += qty
            if not rows:
                raise ValueError("Add at least one purchase item.")

            transport_per_egg = (
                (Decimal(transport_cost) / Decimal(total_qty)).quantize(UNIT_COST_QUANT)
                if total_qty and transport_cost > ZERO else Decimal("0.000000")
            )

            with transaction.atomic():
                purchase = EggPOSPurchase.objects.create(
                    supplier=supplier,
                    purchase_date=purchase_date,
                    reference=(request.POST.get("reference") or "").strip(),
                    transport_cost=transport_cost,
                    transport_payment_method=transport_method,
                    notes=(request.POST.get("notes") or "").strip(),
                    created_by=request.user,
                )
                for number, (product, qty, unit_cost, expiry) in enumerate(rows, start=1):
                    item = EggPOSPurchaseItem.objects.create(
                        purchase=purchase,
                        product=product,
                        quantity=qty,
                        unit_cost=unit_cost,
                    )
                    landed_unit_cost = (Decimal(unit_cost) + transport_per_egg).quantize(UNIT_COST_QUANT)
                    lot = EggPOSInventoryLot.objects.create(
                        product=product,
                        source_type="purchase",
                        supplier=supplier,
                        purchase=purchase,
                        lot_code=make_lot_code("PUR", purchase.id, number),
                        received_date=purchase_date,
                        expiry_date=expiry,
                        quantity_received=qty,
                        quantity_remaining=qty,
                        unit_cost=landed_unit_cost,
                        notes=(
                            f"Purchase #{purchase.id} {purchase.reference} | "
                            f"Base Rs {unit_cost:.4f}/egg + transport Rs {transport_per_egg:.4f}/egg"
                        ).strip(),
                    )
                    item.lot = lot
                    item.save(update_fields=["lot"])
                sync_purchase(purchase)

            messages.success(
                request,
                f"Purchase #{purchase.id} saved. Transport Rs {transport_cost:,.2f} was included in FIFO landed cost.",
            )
            return redirect("egg_pos_purchase_list")
        except Exception as error:
            messages.error(request, str(error))

    return render(request, "api/egg_pos_purchase_add.html", {
        "products": products_qs,
        "suppliers": suppliers_qs,
        "transport_methods": EggPOSPurchase.TRANSPORT_PAYMENT_CHOICES,
        "today": timezone.localdate(),
    })


@login_required
@require_http_methods(["GET", "POST"])
def edit_purchase(request, purchase_id):
    """Admin-safe correction of purchase value/transport without changing physical stock.

    Product and quantity are intentionally locked because changing them after FIFO
    activity would require a stock-adjustment workflow. Cost corrections are pushed
    through inventory lots, historical FIFO allocations, sale COGS, journals, and
    any *unpaid* posted commission periods. A paid/part-paid commission period blocks
    the correction so accounting cannot silently diverge.
    """
    if not can_manage(request.user):
        return _deny(request, "Only Egg POS management can correct purchases.")

    purchase = get_object_or_404(
        EggPOSPurchase.objects
        .select_related("supplier", "created_by")
        .prefetch_related(
            "items__product",
            "items__lot__sale_allocations__sale_item__sale__created_by",
            "payments",
        ),
        pk=purchase_id,
    )
    items = list(purchase.items.all())
    suppliers_qs = EggPOSSupplier.objects.filter(is_active=True).order_by("name")

    for item in items:
        item.current_line_total = money(item.line_total)
        item.remaining_eggs = int(item.lot.quantity_remaining or 0) if item.lot_id else 0
        item.sold_eggs = max(int(item.quantity or 0) - item.remaining_eggs, 0)

    if request.method == "POST":
        try:
            reason = (request.POST.get("correction_reason") or "").strip()
            if not reason:
                raise ValueError("Enter a reason for the correction so the audit trail is clear.")

            supplier = get_object_or_404(
                EggPOSSupplier,
                pk=request.POST.get("supplier"),
                is_active=True,
            )
            transport_cost = _parse_money(
                request.POST.get("transport_cost") or "0",
                "Transport cost",
                allow_zero=True,
            )
            transport_method = (request.POST.get("transport_payment_method") or "cash").strip()
            valid_transport_methods = {value for value, _ in EggPOSPurchase.TRANSPORT_PAYMENT_CHOICES}
            if transport_method not in valid_transport_methods:
                raise ValueError("Select a valid transport payment source.")

            posted_item_ids = request.POST.getlist("item_id")
            posted_totals = request.POST.getlist("line_total_cost")
            if len(posted_item_ids) != len(items) or len(posted_totals) != len(items):
                raise ValueError("Purchase items do not match. Refresh the page and try again.")

            new_line_totals = {}
            for idx, raw_id in enumerate(posted_item_ids):
                try:
                    item_id = int(raw_id)
                except (TypeError, ValueError):
                    raise ValueError("Invalid purchase item.")
                if not any(item.id == item_id for item in items):
                    raise ValueError("A purchase item does not belong to this purchase.")
                new_line_totals[item_id] = _parse_money(
                    posted_totals[idx],
                    "Correct total cost",
                )

            total_qty = sum(int(item.quantity or 0) for item in items)
            if total_qty <= 0:
                raise ValueError("Purchase quantity is invalid.")
            transport_per_egg = (
                (Decimal(transport_cost) / Decimal(total_qty)).quantize(UNIT_COST_QUANT)
                if transport_cost > ZERO else Decimal("0.000000")
            )

            # Check paid commission history before touching COGS. Unpaid periods can
            # be recalculated automatically, but paid periods require a formal
            # adjustment and therefore block this correction.
            affected_sales = {}
            for item in items:
                if not item.lot_id:
                    continue
                for allocation in item.lot.sale_allocations.all():
                    sale = allocation.sale_item.sale
                    affected_sales[sale.id] = sale

            locked_periods = []
            for sale in affected_sales.values():
                periods = EggPOSCommissionPeriod.objects.filter(
                    salesperson=sale.created_by,
                    period_start__lte=sale.sale_date,
                    period_end__gte=sale.sale_date,
                ).prefetch_related("payments")
                for period in periods:
                    if Decimal(period.amount_paid or 0) > ZERO:
                        locked_periods.append(period)
            if locked_periods:
                raise ValueError(
                    "This purchase has already affected sale(s) inside a paid/part-paid "
                    "commission period. Correct that commission with an adjustment first, "
                    "then edit the purchase cost."
                )

            old_base = money(purchase.total_amount)
            old_transport = money(purchase.transport_cost)
            old_landed = money(purchase.landed_total)
            new_base = money(sum((new_line_totals[item.id] for item in items), ZERO))
            new_landed = money(new_base + transport_cost)

            with transaction.atomic():
                purchase.supplier = supplier
                purchase.reference = (request.POST.get("reference") or "").strip()
                purchase.transport_cost = transport_cost
                purchase.transport_payment_method = transport_method

                stamp = timezone.localtime().strftime("%d %b %Y %H:%M")
                username = request.user.get_full_name().strip() or request.user.username
                audit = (
                    f"[Correction {stamp} by {username}] "
                    f"Base Rs {old_base:,.2f} -> Rs {new_base:,.2f}; "
                    f"Transport Rs {old_transport:,.2f} -> Rs {transport_cost:,.2f}; "
                    f"Landed Rs {old_landed:,.2f} -> Rs {new_landed:,.2f}. "
                    f"Reason: {reason}"
                )
                purchase.notes = "\n".join(filter(None, [(purchase.notes or "").strip(), audit]))
                purchase.save(update_fields=[
                    "supplier", "reference", "transport_cost",
                    "transport_payment_method", "notes",
                ])

                affected_sale_item_ids = set()
                for item in items:
                    line_total = new_line_totals[item.id]
                    base_unit_cost = (Decimal(line_total) / Decimal(item.quantity)).quantize(UNIT_COST_QUANT)
                    item.unit_cost = base_unit_cost
                    item.save(update_fields=["unit_cost"])

                    if not item.lot_id:
                        continue
                    lot = item.lot
                    landed_unit_cost = (base_unit_cost + transport_per_egg).quantize(UNIT_COST_QUANT)
                    lot.supplier = supplier
                    lot.unit_cost = landed_unit_cost
                    lot.notes = (
                        f"Purchase #{purchase.id} {purchase.reference} | "
                        f"Base Rs {base_unit_cost:.6f}/egg + "
                        f"transport Rs {transport_per_egg:.6f}/egg | corrected {stamp}"
                    ).strip()
                    lot.save(update_fields=["supplier", "unit_cost", "notes"])

                    for allocation in lot.sale_allocations.all():
                        allocation.unit_cost = landed_unit_cost
                        allocation.cogs_amount = money(Decimal(allocation.quantity) * landed_unit_cost)
                        allocation.save(update_fields=["unit_cost", "cogs_amount"])
                        affected_sale_item_ids.add(allocation.sale_item_id)

                affected_sale_ids = set()
                for sale_item in EggPOSSaleItem.objects.filter(
                    id__in=affected_sale_item_ids
                ).prefetch_related("lot_allocations"):
                    sale_item.cogs_amount = money(
                        sum((Decimal(a.cogs_amount or 0) for a in sale_item.lot_allocations.all()), ZERO)
                    )
                    sale_item.save(update_fields=["cogs_amount"])
                    affected_sale_ids.add(sale_item.sale_id)

                for sale in EggPOSSale.objects.filter(id__in=affected_sale_ids).prefetch_related("items"):
                    sale.cogs_total = money(
                        sum((Decimal(item.cogs_amount or 0) for item in sale.items.all()), ZERO)
                    )
                    sale.profit_total = money(Decimal(sale.net_total or 0) - sale.cogs_total)
                    sale.save(update_fields=["cogs_total", "profit_total"])
                    sync_sale_invoice(sale)
                    _refresh_unpaid_commissions_for_date(sale.created_by, sale.sale_date)

                sync_purchase(purchase)

            messages.success(
                request,
                f"Purchase #{purchase.id} corrected: base cost Rs {old_base:,.2f} → "
                f"Rs {new_base:,.2f}; landed inventory Rs {new_landed:,.2f}.",
            )
            return redirect("egg_pos_purchase_list")
        except Exception as error:
            messages.error(request, str(error))

    return render(request, "api/egg_pos_purchase_edit.html", {
        "purchase": purchase,
        "items": items,
        "suppliers": suppliers_qs,
        "transport_methods": EggPOSPurchase.TRANSPORT_PAYMENT_CHOICES,
    })


@login_required
@require_http_methods(["GET", "POST"])
def farm_transfer(request):
    if not can_manage(request.user):
        return _deny(request, "Only Egg POS management can transfer farm eggs.")

    batches = list(
        Batch.objects.filter(
            shed__shed_type="layer",
            is_active=True,
            status="active",
        ).select_related("shed").order_by("-start_date", "-id")
    )
    for batch in batches:
        batch.pos_stock = farm_egg_stock(batch)
    products_qs = EggPOSProduct.objects.filter(is_active=True).order_by("name")

    if request.method == "POST":
        try:
            batch = get_object_or_404(
                Batch.objects.select_related("shed"),
                pk=request.POST.get("batch"),
                shed__shed_type="layer",
                is_active=True,
                status="active",
            )
            transfer_date = _parse_date(request.POST.get("transfer_date"), "Transfer date")
            due_raw = (request.POST.get("payment_due_date") or "").strip()
            payment_due_date = _parse_date(due_raw, "Payment due date") if due_raw else None
            if payment_due_date and payment_due_date < transfer_date:
                raise ValueError("Payment due date cannot be before the transfer date.")

            transport_cost = _parse_money(request.POST.get("transport_cost") or "0", "Transport cost", allow_zero=True)
            transport_method = (request.POST.get("transport_payment_method") or "cash").strip()
            valid_transport_methods = {value for value, _ in EggPOSFarmTransfer.TRANSPORT_PAYMENT_CHOICES}
            if transport_method not in valid_transport_methods:
                raise ValueError("Select a valid transport payment source.")

            product_ids = request.POST.getlist("product_id")
            quantities = request.POST.getlist("quantity")
            costs = request.POST.getlist("unit_cost")
            line_totals = request.POST.getlist("line_total_cost")

            rows = []
            total_qty = 0
            total_value = ZERO
            for index, product_id in enumerate(product_ids):
                if not product_id:
                    continue
                product = get_object_or_404(EggPOSProduct, pk=product_id, is_active=True)
                qty = _parse_positive_int(
                    quantities[index] if index < len(quantities) else None,
                    "Quantity",
                )
                unit_raw = (costs[index] if index < len(costs) else "") or ""
                total_raw = (line_totals[index] if index < len(line_totals) else "") or ""
                if str(total_raw).strip():
                    line_cost = _parse_money(total_raw, "Line transfer cost")
                    unit_cost = (Decimal(line_cost) / Decimal(qty)).quantize(UNIT_COST_QUANT)
                else:
                    unit_cost = _parse_unit_cost(unit_raw, "Farm transfer rate")
                rows.append((product, qty, unit_cost))
                total_qty += qty
                total_value += Decimal(qty) * unit_cost

            if not rows:
                raise ValueError("Add at least one product grading row.")

            position = farm_egg_stock(batch)
            if total_qty > position["available"]:
                raise ValueError(
                    f"Only {position['available']} farm eggs are available for POS transfer; "
                    f"{total_qty} requested."
                )

            transport_per_egg = (
                (Decimal(transport_cost) / Decimal(total_qty)).quantize(UNIT_COST_QUANT)
                if total_qty and transport_cost > ZERO else Decimal("0.000000")
            )

            with transaction.atomic():
                transfer = EggPOSFarmTransfer.objects.create(
                    batch=batch,
                    transfer_date=transfer_date,
                    payment_due_date=payment_due_date,
                    transport_cost=transport_cost,
                    transport_payment_method=transport_method,
                    notes=(request.POST.get("notes") or "").strip(),
                    created_by=request.user,
                )
                transfer.transfer_number = f"RNET-{transfer.id:05d}"
                transfer.save(update_fields=["transfer_number"])

                for number, (product, qty, unit_cost) in enumerate(rows, start=1):
                    item = EggPOSFarmTransferItem.objects.create(
                        transfer=transfer,
                        product=product,
                        quantity=qty,
                        unit_cost=unit_cost,
                    )
                    landed_unit_cost = (Decimal(unit_cost) + transport_per_egg).quantize(UNIT_COST_QUANT)
                    lot = EggPOSInventoryLot.objects.create(
                        product=product,
                        source_type="farm",
                        farm_batch=batch,
                        farm_transfer=transfer,
                        lot_code=make_lot_code("RN", transfer.id, number),
                        received_date=transfer_date,
                        quantity_received=qty,
                        quantity_remaining=qty,
                        unit_cost=landed_unit_cost,
                        notes=(
                            f"RayNoor internal transfer {transfer.transfer_number} | "
                            f"Base Rs {unit_cost:.4f}/egg + transport Rs {transport_per_egg:.4f}/egg"
                        ),
                    )
                    item.lot = lot
                    item.save(update_fields=["lot"])
                sync_farm_transfer(transfer)

            messages.success(
                request,
                f"{transfer.transfer_number} created: {total_qty} eggs, "
                f"Rs {money(total_value):,.2f} payable to RayNoor Egg Production; "
                f"Rs {transport_cost:,.2f} transport included in FIFO landed cost.",
            )
            return redirect("egg_pos_farm_transfer_detail", transfer_id=transfer.id)
        except Exception as error:
            messages.error(request, str(error))

    all_transfers = list(
        EggPOSFarmTransfer.objects
        .filter(is_voided=False)
        .select_related("batch", "batch__shed", "created_by")
        .prefetch_related("items__product", "payments")
        .order_by("-transfer_date", "-id")
    )
    transfers = all_transfers[:40]
    total_transfer_value = money(sum((Decimal(t.total_amount or 0) for t in all_transfers), ZERO))
    total_paid = money(sum((Decimal(t.amount_paid or 0) for t in all_transfers), ZERO))
    total_outstanding = money(sum((Decimal(t.balance_due or 0) for t in all_transfers), ZERO))
    outstanding_count = sum(1 for t in all_transfers if t.balance_due > ZERO)

    return render(request, "api/egg_pos_farm_transfer.html", {
        "batches": batches,
        "products": products_qs,
        "today": timezone.localdate(),
        "transfers": transfers,
        "total_transfer_value": total_transfer_value,
        "total_paid": total_paid,
        "total_outstanding": total_outstanding,
        "outstanding_count": outstanding_count,
        "transport_methods": EggPOSFarmTransfer.TRANSPORT_PAYMENT_CHOICES,
    })


@login_required
def farm_transfer_detail(request, transfer_id):
    if not can_manage(request.user):
        return _deny(request, "Only Egg POS management can view internal supplier balances.")

    transfer = get_object_or_404(
        EggPOSFarmTransfer.objects
        .select_related("batch", "batch__shed", "created_by")
        .prefetch_related("items__product", "payments__recorded_by"),
        pk=transfer_id,
    )
    editable, edit_reason = _farm_transfer_is_editable(transfer)
    return render(request, "api/egg_pos_farm_transfer_detail.html", {
        "transfer": transfer,
        "transfer_editable": editable,
        "transfer_edit_reason": edit_reason,
        "payment_methods": EggPOSFarmTransferPayment.PAYMENT_METHOD_CHOICES,
        "today": timezone.localdate(),
    })


@login_required
@require_POST
def record_farm_transfer_payment(request, transfer_id):
    if not can_manage(request.user):
        return _deny(request, "Only Egg POS management can record internal supplier payments.")

    transfer = get_object_or_404(
        EggPOSFarmTransfer.objects.prefetch_related("items", "payments"),
        pk=transfer_id,
    )
    try:
        if transfer.is_voided:
            raise ValueError("A voided transfer cannot receive payments.")
        balance = money(transfer.balance_due)
        if balance <= ZERO:
            raise ValueError("This internal transfer is already paid in full.")

        amount = _parse_money(request.POST.get("amount"), "Payment amount")
        if amount > balance:
            raise ValueError(f"Payment cannot exceed balance due Rs {balance:,.2f}.")

        method = request.POST.get("payment_method") or "bank_transfer"
        valid_methods = {value for value, _ in EggPOSFarmTransferPayment.PAYMENT_METHOD_CHOICES}
        if method not in valid_methods:
            raise ValueError("Select a valid payment method.")

        payment_date = _parse_date(request.POST.get("payment_date"), "Payment date")
        payment = EggPOSFarmTransferPayment.objects.create(
            transfer=transfer,
            payment_date=payment_date,
            amount=amount,
            payment_method=method,
            reference=(request.POST.get("reference") or "").strip(),
            notes=(request.POST.get("notes") or "").strip(),
            recorded_by=request.user,
        )
        sync_farm_transfer_payment(payment)
        messages.success(
            request,
            f"Payment Rs {amount:,.2f} recorded against {transfer.transfer_number}.",
        )
    except Exception as error:
        messages.error(request, str(error))

    return redirect("egg_pos_farm_transfer_detail", transfer_id=transfer.id)


@login_required
@require_POST
def add_customer(request):
    """Allow every active Egg POS seller to create a customer from checkout."""
    if not can_sell(request.user):
        return JsonResponse({"ok": False, "error": "You do not have Egg POS sale access."}, status=403)

    name = (request.POST.get("name") or "").strip()
    company_name = (request.POST.get("company_name") or "").strip()
    phone = (request.POST.get("phone") or "").strip()
    address = (request.POST.get("address") or "").strip()

    if not name:
        return JsonResponse({"ok": False, "error": "Customer name is required."}, status=400)

    customer = EggPOSCustomer.objects.create(
        name=name,
        company_name=company_name,
        phone=phone,
        address=address,
        created_by=request.user,
    )

    return JsonResponse({
        "ok": True,
        "customer": {
            "id": customer.id,
            "name": customer.name,
            "company_name": customer.company_name,
            "phone": customer.phone,
            "address": customer.address,
            "label": customer.display_label,
        },
    })


@login_required
@require_http_methods(["GET", "POST"])
def new_sale(request):
    if not can_sell(request.user):
        return _deny(request)

    products_qs = list(EggPOSProduct.objects.filter(is_active=True).order_by("name"))
    for product in products_qs:
        product.pos_stock = available_product_stock(product)

    customers_qs = list(
        EggPOSCustomer.objects.filter(is_active=True)
        .order_by("name", "company_name", "id")
    )

    selected_customer_id = None
    selected_customer_raw = (request.GET.get("customer") or "").strip()
    if selected_customer_raw:
        try:
            selected_customer_id = int(selected_customer_raw)
            if not any(customer.id == selected_customer_id for customer in customers_qs):
                selected_customer_id = None
        except (TypeError, ValueError):
            selected_customer_id = None

    if request.method == "POST":
        try:
            sale_date = _parse_date(request.POST.get("sale_date"), "Sale date")
            product_ids = request.POST.getlist("product_id")
            quantities = request.POST.getlist("quantity")
            prices = request.POST.getlist("unit_price")
            sale_units = request.POST.getlist("sale_unit")
            unit_counts = request.POST.getlist("unit_count")
            unit_rates = request.POST.getlist("unit_rate")

            unit_sizes = {"egg": 1, "dozen": 12, "tray": 30, "crate": 360}
            rows = []
            requested_by_product = {}

            for index, product_id in enumerate(product_ids):
                if not product_id:
                    continue

                product = get_object_or_404(EggPOSProduct, pk=product_id, is_active=True)

                # New pack-aware form. The inventory quantity is converted to eggs,
                # while the entered pack rate is preserved exactly for the invoice.
                sale_unit = (sale_units[index] if index < len(sale_units) else "").strip().lower()
                if sale_unit in unit_sizes:
                    unit_size = unit_sizes[sale_unit]
                    unit_count = _parse_positive_int(
                        unit_counts[index] if index < len(unit_counts) else None,
                        "Pack quantity",
                    )
                    unit_rate = _parse_money(
                        unit_rates[index] if index < len(unit_rates) else None,
                        "Sale rate",
                    )
                    qty = unit_count * unit_size
                    line_total = money(Decimal(unit_count) * unit_rate)
                    # Keep the legacy per-egg field populated for compatibility.
                    unit_price = (line_total / Decimal(qty)).quantize(Decimal("0.01"))
                else:
                    # Backward-compatible fallback for older cached forms.
                    sale_unit = "egg"
                    unit_size = 1
                    qty = _parse_positive_int(
                        quantities[index] if index < len(quantities) else None,
                        "Quantity",
                    )
                    unit_count = qty
                    unit_price = _parse_money(
                        prices[index] if index < len(prices) else None,
                        "Sale price",
                    )
                    unit_rate = unit_price
                    line_total = money(Decimal(qty) * unit_price)

                rows.append((
                    product, qty, unit_price, sale_unit, unit_count,
                    unit_size, unit_rate, line_total,
                ))
                if product.id not in requested_by_product:
                    requested_by_product[product.id] = {"product": product, "qty": 0}
                requested_by_product[product.id]["qty"] += qty

            if not rows:
                raise ValueError("Add at least one sale item.")

            # Pre-check combined quantities because the same egg product may appear
            # on multiple invoice lines (e.g. 1 crate + 2 trays + 6 loose eggs).
            for requested in requested_by_product.values():
                product = requested["product"]
                qty = requested["qty"]
                stock = available_product_stock(product, sale_date)
                if stock <= 0:
                    raise ValueError(f"{product.name} is out of stock. Sale was not created.")
                if qty > stock:
                    raise ValueError(
                        f"Only {stock} {product.name} eggs are available for this sale date; "
                        f"this invoice requests {qty}."
                    )

            subtotal = money(sum((row[7] for row in rows), ZERO))
            discount = _parse_money(
                request.POST.get("discount_amount") or "0",
                "Discount",
                allow_zero=True,
            )
            if discount > subtotal:
                raise ValueError("Discount cannot exceed the sale subtotal.")
            net_total = money(subtotal - discount)

            payment_type = (request.POST.get("payment_type") or "full").lower()
            if payment_type not in {"full", "partial", "credit"}:
                raise ValueError("Select a valid payment type.")

            payment_method = request.POST.get("payment_method") or "cash"
            valid_methods = {value for value, _ in EggPOSSalePayment.PAYMENT_METHOD_CHOICES}
            if payment_method not in valid_methods:
                raise ValueError("Select a valid payment method.")

            if payment_type == "full":
                amount_received = net_total
            elif payment_type == "partial":
                amount_received = _parse_money(
                    request.POST.get("amount_received") or "0",
                    "Amount received",
                )
                if amount_received >= net_total:
                    raise ValueError("Partial payment must be less than the net sale.")
            else:
                amount_received = ZERO

            balance_due = money(net_total - amount_received)

            customer = None
            customer_id = (request.POST.get("customer_id") or "").strip()
            if customer_id:
                customer = get_object_or_404(
                    EggPOSCustomer,
                    pk=customer_id,
                    is_active=True,
                )

            # Backward-compatible fallback for an older sale form.
            customer_name = (request.POST.get("customer_name") or "").strip()
            customer_phone = (request.POST.get("customer_phone") or "").strip()
            if customer:
                customer_name = customer.name
                customer_phone = customer.phone

            if balance_due > ZERO and not customer and not customer_name:
                raise ValueError(
                    "Select or add a customer for Partial or Credit sales."
                )

            due_raw = (request.POST.get("credit_due_date") or "").strip()
            due_date = _parse_date(due_raw, "Credit due date") if due_raw else None
            if due_date and due_date < sale_date:
                raise ValueError("Credit due date cannot be before the sale date.")

            with transaction.atomic():
                # CRITICAL STOCK GUARD:
                # lock the actual inventory lots and re-check stock BEFORE the
                # sale/invoice is created. This prevents zero-stock sales and
                # two staff users selling the same last eggs simultaneously.
                for requested in requested_by_product.values():
                    product = requested["product"]
                    qty = requested["qty"]
                    locked_lots = list(
                        EggPOSInventoryLot.objects.select_for_update()
                        .filter(
                            product=product,
                            quantity_remaining__gt=0,
                            received_date__lte=sale_date,
                        )
                        .filter(
                            Q(expiry_date__isnull=True)
                            | Q(expiry_date__gte=sale_date)
                        )
                        .order_by("received_date", "id")
                    )
                    locked_available = sum(
                        int(lot.quantity_remaining or 0)
                        for lot in locked_lots
                    )
                    if locked_available <= 0:
                        raise ValueError(
                            f"{product.name} is out of stock. Sale was not created."
                        )
                    if qty > locked_available:
                        raise ValueError(
                            f"Only {locked_available} {product.name} eggs are available now; "
                            f"this invoice requests {qty}. Stock changed before checkout, "
                            "so the sale was not created."
                        )

                sale = EggPOSSale.objects.create(
                    sale_date=sale_date,
                    customer=customer,
                    customer_name=customer_name,
                    customer_phone=customer_phone,
                    payment_method="credit" if payment_type == "credit" else payment_method,
                    credit_due_date=due_date,
                    discount_amount=discount,
                    subtotal=subtotal,
                    net_total=net_total,
                    notes=(request.POST.get("notes") or "").strip(),
                    created_by=request.user,
                )
                sale.sale_number = f"RNE-{sale.id:05d}"
                sale.save(update_fields=["sale_number"])

                cogs_total = ZERO
                for (
                    product, qty, unit_price, sale_unit, unit_count,
                    unit_size, unit_rate, line_total,
                ) in rows:
                    item = EggPOSSaleItem.objects.create(
                        sale=sale,
                        product=product,
                        quantity=qty,
                        sale_unit=sale_unit,
                        unit_count=unit_count,
                        unit_size=unit_size,
                        unit_rate=unit_rate,
                        unit_price=unit_price,
                        line_total=line_total,
                    )
                    cogs_total += allocate_fifo_to_sale_item(item, sale.sale_date)

                sale.cogs_total = money(cogs_total)
                sale.profit_total = money(sale.net_total - sale.cogs_total)
                sale.save(update_fields=["cogs_total", "profit_total"])
                sync_sale_invoice(sale)

                if amount_received > ZERO:
                    payment = EggPOSSalePayment.objects.create(
                        sale=sale,
                        payment_date=sale.sale_date,
                        amount=amount_received,
                        payment_method=payment_method,
                        notes="Payment received at checkout.",
                        recorded_by=request.user,
                    )
                    sync_sale_payment(payment)

            messages.success(
                request,
                f"Sale {sale.sale_number} completed. Balance due Rs {balance_due:,.2f}.",
            )
            return redirect("egg_pos_sale_invoice", sale_id=sale.id)

        except Exception as error:
            messages.error(request, str(error))
            # Always refresh displayed stock after a rejected checkout.
            for product in products_qs:
                product.pos_stock = available_product_stock(product)

    return render(request, "api/egg_pos_sale_new.html", {
        "products": products_qs,
        "customers": customers_qs,
        "selected_customer_id": selected_customer_id,
        "payment_methods": EggPOSSalePayment.PAYMENT_METHOD_CHOICES,
        "today": timezone.localdate(),
    })


@login_required
@require_http_methods(["GET", "POST"])
def customers(request):
    """
    Shared Egg POS customer directory.

    Both sales staff and management can see customer contact details and the
    customer's overall outstanding balance. Sales staff do NOT get another
    salesperson's invoice detail; their invoice rows remain limited to sales
    created by their own login.
    """
    if not can_sell(request.user):
        return _deny(request)

    if request.method == "POST":
        try:
            name = (request.POST.get("name") or "").strip()
            company_name = (request.POST.get("company_name") or "").strip()
            phone = (request.POST.get("phone") or "").strip()
            address = (request.POST.get("address") or "").strip()
            notes = (request.POST.get("notes") or "").strip()

            if not name:
                raise ValueError("Customer name is required.")

            customer = EggPOSCustomer.objects.create(
                name=name,
                company_name=company_name,
                phone=phone,
                address=address,
                notes=notes,
                created_by=request.user,
            )
            messages.success(request, f"Customer {customer.display_label} added.")
            return redirect("egg_pos_customer_detail", customer_id=customer.id)

        except Exception as error:
            messages.error(request, str(error))

    q = (request.GET.get("q") or "").strip()
    balance_filter = (request.GET.get("balance") or "all").strip().lower()
    if balance_filter not in {"all", "outstanding", "clear"}:
        balance_filter = "all"

    manage = can_manage(request.user)

    all_customers = list(
        EggPOSCustomer.objects.filter(is_active=True)
        .prefetch_related("sales__payments")
        .order_by("name", "company_name", "id")
    )

    all_rows = [
        _customer_row(customer, request.user, manage)
        for customer in all_customers
    ]

    total_customer_count = len(all_rows)
    customers_with_balance = sum(
        1
        for row in all_rows
        if row["overall"]["outstanding"] > ZERO
    )
    total_outstanding = money(
        sum(
            (row["overall"]["outstanding"] for row in all_rows),
            ZERO,
        )
    )

    rows = all_rows

    if q:
        q_lower = q.lower()
        rows = [
            row
            for row in rows
            if q_lower in (row["customer"].name or "").lower()
            or q_lower in (row["customer"].company_name or "").lower()
            or q_lower in (row["customer"].phone or "").lower()
        ]

    if balance_filter == "outstanding":
        rows = [
            row
            for row in rows
            if row["overall"]["outstanding"] > ZERO
        ]
    elif balance_filter == "clear":
        rows = [
            row
            for row in rows
            if row["overall"]["outstanding"] <= ZERO
        ]

    return render(request, "api/egg_pos_customers.html", {
        "rows": rows,
        "q": q,
        "balance_filter": balance_filter,
        "total_customer_count": total_customer_count,
        "customers_with_balance": customers_with_balance,
        "total_outstanding": total_outstanding,
        "can_manage_pos": manage,
    })


@login_required
def customer_detail(request, customer_id):
    if not can_sell(request.user):
        return _deny(request)

    customer = get_object_or_404(
        EggPOSCustomer.objects.prefetch_related(
            "sales__payments__recorded_by",
            "sales__items__product",
        ),
        pk=customer_id,
        is_active=True,
    )

    manage = can_manage(request.user)
    all_sales = list(customer.sales.filter(is_reversed=False))
    overall = _customer_sales_totals(all_sales)

    if manage:
        visible_sales = all_sales
    else:
        visible_sales = [
            sale
            for sale in all_sales
            if sale.created_by_id == request.user.id
        ]

    visible_sales.sort(
        key=lambda sale: (sale.sale_date, sale.id),
        reverse=True,
    )
    visible = _customer_sales_totals(visible_sales)

    return render(request, "api/egg_pos_customer_detail.html", {
        "customer": customer,
        "overall": overall,
        "visible": visible,
        "sales": visible_sales,
        "can_manage_pos": manage,
    })


@login_required
def sales(request):
    if not can_sell(request.user):
        return _deny(request)
    qs=EggPOSSale.objects.select_related("created_by").prefetch_related("items__product","payments")
    if not can_manage(request.user):
        qs=qs.filter(created_by=request.user)
    return render(request,"api/egg_pos_sales.html",{"sales":qs.order_by("-sale_date","-id")[:200],"can_manage_pos":can_manage(request.user)})

@login_required
def sale_detail(request, sale_id):
    if not can_sell(request.user):
        return _deny(request)
    sale=get_object_or_404(EggPOSSale.objects.select_related("created_by").prefetch_related("items__product","items__lot_allocations__lot","payments__recorded_by"),pk=sale_id)
    if not can_manage(request.user) and sale.created_by_id != request.user.id:
        return _deny(request,"You can only view Egg POS sales created by your login.")
    return render(request,"api/egg_pos_sale_detail.html",{"sale":sale,"can_manage_pos":can_manage(request.user),"payment_methods":EggPOSSalePayment.PAYMENT_METHOD_CHOICES,"today":timezone.localdate()})

@login_required
@require_POST
def record_sale_payment(request, sale_id):
    if not can_sell(request.user):
        return _deny(request)
    sale=get_object_or_404(EggPOSSale.objects.prefetch_related("payments"),pk=sale_id)
    if sale.is_reversed:
        messages.error(request, "A reversed invoice cannot receive payments.")
        return redirect("egg_pos_sale_detail", sale_id=sale.id)
    if not can_manage(request.user) and sale.created_by_id != request.user.id:
        return _deny(request,"You can only receive payment against your own sales.")
    try:
        balance=money(sale.balance_due)
        if balance <= ZERO:
            raise ValueError("This invoice is already paid in full.")
        amount=_parse_money(request.POST.get("amount"),"Payment amount")
        if amount > balance:
            raise ValueError(f"Payment cannot exceed balance due Rs {balance:,.2f}.")
        method=request.POST.get("payment_method") or "cash"
        if method not in {v for v,_ in EggPOSSalePayment.PAYMENT_METHOD_CHOICES}:
            raise ValueError("Select a valid payment method.")
        payment_date=_parse_date(request.POST.get("payment_date"),"Payment date")
        payment = EggPOSSalePayment.objects.create(sale=sale,payment_date=payment_date,amount=amount,payment_method=method,reference=(request.POST.get("reference") or "").strip(),notes=(request.POST.get("notes") or "").strip(),recorded_by=request.user)
        sync_sale_payment(payment)
        messages.success(request,f"Payment Rs {amount:,.2f} recorded for {sale.sale_number}.")
    except Exception as error:
        messages.error(request,str(error))
    return redirect("egg_pos_sale_detail",sale_id=sale.id)


@login_required
@require_POST
def reverse_pos_sale(request, sale_id):
    if not can_manage(request.user):
        return _deny(request, "Only a POS manager/admin can reverse a sale.")
    sale = get_object_or_404(EggPOSSale, pk=sale_id)
    try:
        reverse_sale(sale, request.user, request.POST.get("reason"))
        messages.success(request, f"{sale.sale_number} reversed. Stock, cash, revenue and COGS effects were removed.")
    except Exception as error:
        messages.error(request, str(error))
    return redirect("egg_pos_sale_detail", sale_id=sale.id)


@login_required
def sale_invoice(request, sale_id):
    if not can_sell(request.user):
        return _deny(request)
    sale=get_object_or_404(EggPOSSale.objects.select_related("created_by").prefetch_related("items__product","payments__recorded_by"),pk=sale_id)
    if not can_manage(request.user) and sale.created_by_id != request.user.id:
        return _deny(request,"You can only view invoices for your own sales.")
    return render(request,"api/egg_pos_sale_invoice.html",{"sale":sale,"can_manage_pos":can_manage(request.user)})


@login_required
@require_http_methods(["GET", "POST"])
def staff_access(request):
    if not is_admin(request.user):
        return _deny(request, "Only Admin can manage Egg POS staff access.")

    if request.method == "POST":
        try:
            user = get_object_or_404(User, pk=request.POST.get("user"))
            access, _ = EggPOSUserAccess.objects.get_or_create(user=user)
            requested_active = request.POST.get("is_active") == "on"
            requested_sell = request.POST.get("can_sell") == "on"
            requested_manage = request.POST.get("can_manage") == "on"
            commission_percent = _parse_money(
                request.POST.get("commission_percent") or "0",
                "Commission %",
                allow_zero=True,
            )
            if commission_percent > Decimal("100.00"):
                raise ValueError("Commission % cannot exceed 100%.")

            # Selecting any permission means this user must have active
            # Egg POS access. To fully disable access, Admin simply clears
            # Active, Can Sell and Can Manage.
            access.can_sell = requested_sell
            access.can_manage = requested_manage
            access.is_active = requested_active or requested_sell or requested_manage
            access.commission_percent = commission_percent
            access.save()
            messages.success(request, f"Egg POS access updated for {user.username}.")
            return redirect("egg_pos_staff_access")
        except Exception as error:
            messages.error(request, str(error))

    users = User.objects.filter(is_active=True).order_by("username")
    access_map = {row.user_id: row for row in EggPOSUserAccess.objects.select_related("user")}
    rows = [{"user": user, "access": access_map.get(user.id)} for user in users]
    return render(request, "api/egg_pos_staff_access.html", {"rows": rows})


def _egg_pos_salespeople():
    return User.objects.filter(
        is_active=True,
        egg_pos_access__is_active=True,
        egg_pos_access__can_sell=True,
    ).select_related("egg_pos_access").order_by("username")




def _commission_summary(user, today=None):
    today = today or timezone.localdate()
    month_start = today.replace(day=1)
    earned = money(
        EggPOSCommissionPeriod.objects.filter(
            salesperson=user,
            period_end__gte=month_start,
            period_end__lte=today,
        ).aggregate(total=Sum("commission_amount"))["total"] or ZERO
    )
    received = money(
        EggPOSCommissionPayment.objects.filter(
            commission__salesperson=user,
            payment_date__gte=month_start,
            payment_date__lte=today,
        ).aggregate(total=Sum("amount"))["total"] or ZERO
    )
    balance = money(staff_commission_balance(user))
    return {
        "month_earned": earned,
        "month_received": received,
        "balance_due": balance,
    }


def _pending_handover_total(user):
    return money(
        EggPOSCashHandoverRequest.objects.filter(
            salesperson=user,
            status="pending",
        ).aggregate(total=Sum("amount"))["total"] or ZERO
    )


def _refresh_unpaid_commissions_for_date(user, activity_date):
    refreshed = 0
    locked = 0
    periods = EggPOSCommissionPeriod.objects.filter(
        salesperson=user,
        period_start__lte=activity_date,
        period_end__gte=activity_date,
    ).prefetch_related("payments")
    for commission in periods:
        if Decimal(commission.amount_paid) > ZERO:
            locked += 1
            continue
        snapshot = commission_preview(user, commission.period_start, commission.period_end)
        commission.sales_revenue = snapshot["sales_revenue"]
        commission.cogs = snapshot["cogs"]
        commission.selling_expenses = snapshot["selling_expenses"]
        commission.commissionable_profit = snapshot["commissionable_profit"]
        commission.commission_percent = snapshot["commission_percent"]
        commission.commission_amount = snapshot["commission_amount"]
        commission.save(update_fields=[
            "sales_revenue", "cogs", "selling_expenses", "commissionable_profit",
            "commission_percent", "commission_amount",
        ])
        sync_commission_period(commission)
        refreshed += 1
    return refreshed, locked

def _report_range(request):
    """Resolve common accounting periods while preserving custom date ranges."""
    today = timezone.localdate()
    period = (request.GET.get("period") or "").strip().lower()
    month_start = today.replace(day=1)

    if period == "today":
        return today, today
    if period == "this_month":
        return month_start, today
    if period == "last_month":
        last_month_end = month_start - timedelta(days=1)
        return last_month_end.replace(day=1), last_month_end

    try:
        start_date = _parse_date(request.GET.get("start"), "Start date", default=month_start)
        end_date = _parse_date(request.GET.get("end"), "End date", default=today)
    except ValueError:
        start_date, end_date = month_start, today
    if end_date < start_date:
        start_date, end_date = end_date, start_date
    return start_date, end_date


def _report_period_key(request, start_date, end_date):
    requested = (request.GET.get("period") or "").strip().lower()
    if requested in {"today", "this_month", "last_month"}:
        return requested
    if request.GET.get("start") or request.GET.get("end"):
        return "custom"

    today = timezone.localdate()
    if start_date == today and end_date == today:
        return "today"
    if start_date == today.replace(day=1) and end_date == today:
        return "this_month"
    return "custom"


@login_required
@require_http_methods(["GET", "POST"])
def supplier_detail(request, supplier_id):
    if not can_manage(request.user):
        return _deny(request, "Only Egg POS management can view supplier ledgers.")

    supplier = get_object_or_404(EggPOSSupplier, pk=supplier_id)
    ensure_default_accounts()

    if request.method == "POST":
        try:
            balance = money(gl_supplier_balance(supplier))
            if balance <= ZERO:
                raise ValueError("This supplier has no outstanding payable in the ledger.")
            amount = _parse_money(request.POST.get("amount"), "Payment amount")
            if amount > balance:
                raise ValueError(f"Payment cannot exceed supplier payable Rs {balance:,.2f}.")
            method = (request.POST.get("payment_method") or "cash").strip()
            valid_methods = {value for value, _ in EggPOSSupplierPayment.PAYMENT_METHOD_CHOICES}
            if method not in valid_methods:
                raise ValueError("Select a valid payment method.")
            purchase = None
            purchase_id = (request.POST.get("purchase") or "").strip()
            if purchase_id:
                purchase = get_object_or_404(EggPOSPurchase, pk=purchase_id, supplier=supplier)
            payment = EggPOSSupplierPayment.objects.create(
                supplier=supplier,
                purchase=purchase,
                payment_date=_parse_date(request.POST.get("payment_date"), "Payment date"),
                amount=amount,
                payment_method=method,
                reference=(request.POST.get("reference") or "").strip(),
                notes=(request.POST.get("notes") or "").strip(),
                recorded_by=request.user,
            )
            sync_supplier_payment(payment)
            messages.success(request, f"Payment Rs {amount:,.2f} recorded for {supplier.name}.")
            return redirect("egg_pos_supplier_detail", supplier_id=supplier.id)
        except Exception as error:
            messages.error(request, str(error))

    purchases = list(
        supplier.egg_pos_purchases.select_related("created_by")
        .prefetch_related("items__product", "payments")
        .order_by("-purchase_date", "-id")
    )
    payments = supplier.payments.select_related("purchase", "recorded_by").order_by("-payment_date", "-id")[:100]
    ledger_lines = list(
        party_activity(
            party_type="supplier",
            party_id=supplier.id,
            account_codes=["2000"],
        )
    )
    running = ZERO
    ledger_rows = []
    for row in ledger_lines:
        running = money(running + Decimal(row.credit or 0) - Decimal(row.debit or 0))
        ledger_rows.append({"line": row, "balance": running})

    return render(request, "api/egg_pos_supplier_detail.html", {
        "supplier": supplier,
        "purchases": purchases,
        "payments": payments,
        "ledger_rows": reversed(ledger_rows),
        "balance": money(gl_supplier_balance(supplier)),
        "payment_methods": EggPOSSupplierPayment.PAYMENT_METHOD_CHOICES,
        "today": timezone.localdate(),
    })


@login_required
@require_http_methods(["GET", "POST"])
def expenses(request):
    if not can_sell(request.user):
        return _deny(request)

    manage = can_manage(request.user)
    salespeople = list(_egg_pos_salespeople()) if manage else [request.user]

    if request.method == "POST":
        try:
            if manage:
                used_by = get_object_or_404(User, pk=request.POST.get("used_by"), is_active=True)
                if not can_sell(used_by):
                    raise ValueError("Selected staff member does not have active Egg POS sales access.")
            else:
                used_by = request.user

            expense_type = (request.POST.get("expense_type") or "fuel").strip()
            if expense_type not in {value for value, _ in EggPOSExpense.EXPENSE_TYPE_CHOICES}:
                raise ValueError("Select a valid expense type.")
            payment_source = (request.POST.get("payment_source") or "sales_collection").strip()
            if payment_source not in {value for value, _ in EggPOSExpense.PAYMENT_SOURCE_CHOICES}:
                raise ValueError("Select a valid payment source.")

            expense_date = _parse_date(request.POST.get("expense_date"), "Expense date")
            expense_amount = _parse_money(request.POST.get("amount"), "Expense amount")
            if manage and payment_source == "sales_collection":
                cash_available = money(staff_cash_balance(used_by, as_of=expense_date))
                if expense_amount > cash_available:
                    raise ValueError(
                        f"{used_by.username} has only Rs {cash_available:,.2f} sales cash available on this date. "
                        "Use Paid Personally if the salesperson used their own money."
                    )

            status = "approved" if manage else "pending"
            expense = EggPOSExpense.objects.create(
                expense_date=expense_date,
                expense_type=expense_type,
                amount=expense_amount,
                used_by=used_by,
                payment_source=payment_source,
                affects_commission=request.POST.get("affects_commission", "on") == "on",
                reference=(request.POST.get("reference") or "").strip(),
                notes=(request.POST.get("notes") or "").strip(),
                status=status,
                entered_by=request.user,
                approved_by=request.user if manage else None,
                approved_at=timezone.now() if manage else None,
            )
            if manage:
                sync_expense(expense)
                refreshed, locked = (0, 0)
                if expense.affects_commission:
                    refreshed, locked = _refresh_unpaid_commissions_for_date(expense.used_by, expense.expense_date)
                suffix = f" Commission recalculated for {refreshed} unpaid posted period(s)." if refreshed else ""
                if locked:
                    suffix += f" {locked} paid/part-paid period(s) were left unchanged."
                messages.success(request, f"{expense.get_expense_type_display()} Rs {expense.amount:,.2f} approved and posted.{suffix}")
            else:
                messages.success(request, f"Expense Rs {expense.amount:,.2f} submitted for approval.")
            return redirect("egg_pos_expenses")
        except Exception as error:
            messages.error(request, str(error))

    qs = EggPOSExpense.objects.select_related("used_by", "entered_by", "approved_by")
    if not manage:
        qs = qs.filter(used_by=request.user)
    rows = list(qs.order_by("-expense_date", "-id")[:200])
    pending_total = money(sum((Decimal(row.amount or 0) for row in rows if row.status == "pending"), ZERO))
    approved_total = money(sum((Decimal(row.amount or 0) for row in rows if row.status == "approved"), ZERO))

    return render(request, "api/egg_pos_expenses.html", {
        "expenses": rows,
        "salespeople": salespeople,
        "expense_types": EggPOSExpense.EXPENSE_TYPE_CHOICES,
        "payment_sources": EggPOSExpense.PAYMENT_SOURCE_CHOICES,
        "today": timezone.localdate(),
        "can_manage_pos": manage,
        "pending_total": pending_total,
        "approved_total": approved_total,
        "cash_to_hand_over": money(staff_cash_balance(request.user)) if not manage else ZERO,
    })


@login_required
@require_POST
def approve_expense(request, expense_id):
    if not can_manage(request.user):
        return _deny(request, "Only Egg POS management can approve expenses.")
    expense = get_object_or_404(EggPOSExpense.objects.select_related("used_by", "entered_by"), pk=expense_id)
    try:
        if expense.status != "pending":
            raise ValueError("Only pending expenses can be approved or rejected.")
        action = (request.POST.get("action") or "approve").strip().lower()
        posted_source = (request.POST.get("payment_source") or "").strip()
        if posted_source:
            valid_sources = {value for value, _ in EggPOSExpense.PAYMENT_SOURCE_CHOICES}
            if posted_source not in valid_sources:
                raise ValueError("Select a valid payment source.")
            expense.payment_source = posted_source
        if action == "approve":
            if expense.payment_source == "sales_collection":
                cash_available = money(staff_cash_balance(expense.used_by, as_of=expense.expense_date))
                if Decimal(expense.amount or 0) > cash_available:
                    raise ValueError(
                        f"{expense.used_by.username} has only Rs {cash_available:,.2f} sales cash available on this date. "
                        "Change the payment source to personal/company payment before approval."
                    )
            with transaction.atomic():
                expense.status = "approved"
                expense.approved_by = request.user
                expense.approved_at = timezone.now()
                expense.rejection_reason = ""
                expense.save(update_fields=["status", "approved_by", "approved_at", "rejection_reason", "payment_source", "updated_at"])
                sync_expense(expense)
                refreshed, locked = (0, 0)
                if expense.affects_commission:
                    refreshed, locked = _refresh_unpaid_commissions_for_date(expense.used_by, expense.expense_date)
            suffix = f" Commission recalculated for {refreshed} unpaid posted period(s)." if refreshed else ""
            if locked:
                suffix += f" {locked} paid/part-paid period(s) were not changed and may need an adjustment."
            messages.success(request, f"Expense Rs {expense.amount:,.2f} approved. It now reduces {expense.used_by.username}'s cash/commission as applicable.{suffix}")
        elif action == "reject":
            expense.status = "rejected"
            expense.approved_by = request.user
            expense.approved_at = timezone.now()
            expense.rejection_reason = (request.POST.get("rejection_reason") or "").strip()
            expense.save(update_fields=["status", "approved_by", "approved_at", "rejection_reason", "updated_at"])
            messages.success(request, "Expense rejected; no accounting entry was posted.")
        else:
            raise ValueError("Invalid approval action.")
    except Exception as error:
        messages.error(request, str(error))
    return redirect("egg_pos_expenses")


@login_required
@require_http_methods(["GET", "POST"])
def cash_settlements(request):
    if not can_sell(request.user):
        return _deny(request)

    manage = can_manage(request.user)
    today = timezone.localdate()
    start_date, end_date = _report_range(request)
    period_key = _report_period_key(request, start_date, end_date)
    salespeople = list(_egg_pos_salespeople()) if manage else [request.user]

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip().lower()
        try:
            if action == "submit_handover":
                if manage:
                    raise ValueError("Management can record cash directly; salesperson submissions are for sales staff.")
                handover_date = _parse_date(request.POST.get("handover_date"), "Handover date", default=today)
                current = money(staff_cash_balance(request.user, as_of=handover_date))
                pending = _pending_handover_total(request.user)
                available_to_submit = money(max(Decimal(current) - Decimal(pending), ZERO))
                if available_to_submit <= ZERO:
                    raise ValueError("You have no additional cash available to submit. Wait for pending handovers to be confirmed.")
                amount = _parse_money(request.POST.get("amount"), "Handover amount")
                if amount > available_to_submit:
                    raise ValueError(
                        f"You can submit up to Rs {available_to_submit:,.2f}. "
                        f"Rs {pending:,.2f} is already awaiting confirmation."
                    )
                destination = (request.POST.get("destination") or "cash").strip()
                if destination not in {value for value, _ in EggPOSCashSettlement.DESTINATION_CHOICES}:
                    raise ValueError("Select Cash Handover or Bank Transfer.")
                handover = EggPOSCashHandoverRequest.objects.create(
                    salesperson=request.user,
                    handover_date=handover_date,
                    amount=amount,
                    destination=destination,
                    reference=(request.POST.get("reference") or "").strip(),
                    notes=(request.POST.get("notes") or "").strip(),
                )
                messages.success(
                    request,
                    f"Rs {handover.amount:,.2f} submitted for confirmation. Your official cash balance changes only after management confirms receipt.",
                )
                return redirect("egg_pos_cash_settlements")

            if not manage:
                raise ValueError("Only Egg POS management can confirm or reject cash handovers.")

            if action == "approve_handover":
                handover = get_object_or_404(
                    EggPOSCashHandoverRequest.objects.select_related("salesperson"),
                    pk=request.POST.get("handover_id"),
                )
                if handover.status != "pending":
                    raise ValueError("This handover has already been reviewed.")
                current = money(staff_cash_balance(handover.salesperson, as_of=handover.handover_date))
                if current <= ZERO or Decimal(handover.amount) > Decimal(current):
                    raise ValueError(
                        f"{handover.salesperson.username}'s available sales cash on {handover.handover_date} is Rs {current:,.2f}. "
                        "Review their cash ledger before confirming."
                    )
                with transaction.atomic():
                    settlement = EggPOSCashSettlement.objects.create(
                        salesperson=handover.salesperson,
                        settlement_date=handover.handover_date,
                        amount=handover.amount,
                        destination=handover.destination,
                        reference=handover.reference,
                        notes=handover.notes,
                        recorded_by=request.user,
                    )
                    sync_cash_settlement(settlement)
                    handover.status = "confirmed"
                    handover.reviewed_by = request.user
                    handover.reviewed_at = timezone.now()
                    handover.rejection_reason = ""
                    handover.settlement = settlement
                    handover.save(update_fields=["status", "reviewed_by", "reviewed_at", "rejection_reason", "settlement"])
                messages.success(
                    request,
                    f"Rs {handover.amount:,.2f} confirmed from {handover.salesperson.username}. Their cash-to-hand-over balance was reduced.",
                )
                return redirect(f"{request.path}?staff={handover.salesperson_id}")

            if action == "reject_handover":
                handover = get_object_or_404(
                    EggPOSCashHandoverRequest.objects.select_related("salesperson"),
                    pk=request.POST.get("handover_id"),
                )
                if handover.status != "pending":
                    raise ValueError("This handover has already been reviewed.")
                handover.status = "rejected"
                handover.reviewed_by = request.user
                handover.reviewed_at = timezone.now()
                handover.rejection_reason = (request.POST.get("rejection_reason") or "").strip()
                handover.save(update_fields=["status", "reviewed_by", "reviewed_at", "rejection_reason"])
                messages.success(request, f"Handover submission from {handover.salesperson.username} rejected.")
                return redirect(f"{request.path}?staff={handover.salesperson_id}")

            if action in {"receive_direct", ""}:
                salesperson = get_object_or_404(User, pk=request.POST.get("salesperson"), is_active=True)
                if not can_sell(salesperson):
                    raise ValueError("Selected staff member does not have active Egg POS sales access.")
                settlement_date = _parse_date(request.POST.get("settlement_date"), "Settlement date")
                current = money(staff_cash_balance(salesperson, as_of=settlement_date))
                if current <= ZERO:
                    raise ValueError(f"{salesperson.username} has no positive sales cash to hand over on {settlement_date}.")
                amount = _parse_money(request.POST.get("amount"), "Settlement amount")
                if amount > current:
                    raise ValueError(f"Settlement cannot exceed cash held Rs {current:,.2f} on {settlement_date}.")
                destination = (request.POST.get("destination") or "cash").strip()
                if destination not in {value for value, _ in EggPOSCashSettlement.DESTINATION_CHOICES}:
                    raise ValueError("Select a valid settlement destination.")
                settlement = EggPOSCashSettlement.objects.create(
                    salesperson=salesperson,
                    settlement_date=settlement_date,
                    amount=amount,
                    destination=destination,
                    reference=(request.POST.get("reference") or "").strip(),
                    notes=(request.POST.get("notes") or "").strip(),
                    recorded_by=request.user,
                )
                sync_cash_settlement(settlement)
                messages.success(request, f"Rs {amount:,.2f} received from {salesperson.username}. Their cash-to-hand-over balance was reduced.")
                return redirect(f"{request.path}?staff={salesperson.id}")

            raise ValueError("Invalid cash handover action.")
        except Exception as error:
            messages.error(request, str(error))

    # Management sees everyone; sales staff see only themselves.
    staff_rows = []
    for user in salespeople:
        current_balance = money(staff_cash_balance(user))
        pending_handover = _pending_handover_total(user)
        cash_to_submit = money(max(Decimal(current_balance) - Decimal(pending_handover), ZERO))
        period_lines = JournalLine.objects.filter(
            account__code="1040",
            entry__module="egg_pos",
            party_type="user",
            party_id=user.id,
            entry__entry_date__gte=start_date,
            entry__entry_date__lte=end_date,
        )
        period_totals = period_lines.aggregate(debit=Sum("debit"), credit=Sum("credit"))
        collected = money(period_totals["debit"] or ZERO)
        cleared = money(period_totals["credit"] or ZERO)
        approved_deductions = money(
            EggPOSExpense.objects.filter(
                used_by=user,
                status="approved",
                payment_source="sales_collection",
                expense_date__gte=start_date,
                expense_date__lte=end_date,
            ).aggregate(total=Sum("amount"))["total"] or ZERO
        )
        handovers = money(
            EggPOSCashSettlement.objects.filter(
                salesperson=user,
                settlement_date__gte=start_date,
                settlement_date__lte=end_date,
            ).aggregate(total=Sum("amount"))["total"] or ZERO
        )
        pending_expenses = money(
            EggPOSExpense.objects.filter(used_by=user, status="pending").aggregate(total=Sum("amount"))["total"] or ZERO
        )
        staff_rows.append({
            "user": user,
            "cash_balance": current_balance,
            "pending_handover": pending_handover,
            "cash_to_submit": cash_to_submit,
            "period_collected": collected,
            "period_cleared": cleared,
            "approved_deductions": approved_deductions,
            "handovers": handovers,
            "pending_expenses": pending_expenses,
            "reimbursement": money(staff_reimbursement_balance(user)),
            "commission_payable": money(staff_commission_balance(user)),
        })

    selected = None
    if salespeople:
        if manage and request.GET.get("staff"):
            try:
                selected_id = int(request.GET.get("staff"))
                selected = next((u for u in salespeople if u.id == selected_id), None)
            except (TypeError, ValueError):
                selected = None
        selected = selected or (request.user if not manage else salespeople[0])

    ledger_rows = []
    opening_balance = ZERO
    closing_balance = ZERO
    selected_summary = None
    if selected:
        selected_summary = next((row for row in staff_rows if row["user"].id == selected.id), None)
        opening_totals = JournalLine.objects.filter(
            account__code="1040",
            entry__module="egg_pos",
            party_type="user",
            party_id=selected.id,
            entry__entry_date__lt=start_date,
        ).aggregate(debit=Sum("debit"), credit=Sum("credit"))
        opening_balance = money(Decimal(opening_totals["debit"] or 0) - Decimal(opening_totals["credit"] or 0))
        running = opening_balance
        activity = JournalLine.objects.filter(
            account__code="1040",
            entry__module="egg_pos",
            party_type="user",
            party_id=selected.id,
            entry__entry_date__gte=start_date,
            entry__entry_date__lte=end_date,
        ).select_related("entry", "account").order_by("entry__entry_date", "entry_id", "id")
        for journal_line in activity:
            running = money(running + Decimal(journal_line.debit or 0) - Decimal(journal_line.credit or 0))
            ledger_rows.append({"line": journal_line, "balance": running})
        closing_balance = running

    settlements = EggPOSCashSettlement.objects.select_related("salesperson", "recorded_by")
    requests_qs = EggPOSCashHandoverRequest.objects.select_related("salesperson", "reviewed_by", "settlement")
    if not manage:
        settlements = settlements.filter(salesperson=request.user)
        requests_qs = requests_qs.filter(salesperson=request.user)
    elif selected:
        settlements = settlements.filter(salesperson=selected)
        requests_qs = requests_qs.filter(salesperson=selected)
    settlements = settlements.order_by("-settlement_date", "-id")[:100]
    pending_requests = (
        EggPOSCashHandoverRequest.objects.filter(status="pending").select_related("salesperson").order_by("handover_date", "id")
        if manage else requests_qs.filter(status="pending").order_by("handover_date", "id")
    )
    handover_requests = requests_qs.order_by("-handover_date", "-id")[:100]

    return render(request, "api/egg_pos_cash_settlements.html", {
        "staff_rows": staff_rows,
        "selected": selected,
        "selected_summary": selected_summary,
        "ledger_rows": list(reversed(ledger_rows)),
        "opening_balance": opening_balance,
        "closing_balance": closing_balance,
        "settlements": settlements,
        "handover_requests": handover_requests,
        "pending_requests": pending_requests,
        "destinations": EggPOSCashSettlement.DESTINATION_CHOICES,
        "today": today,
        "start_date": start_date,
        "end_date": end_date,
        "period_key": period_key,
        "can_manage_pos": manage,
    })


@login_required
@require_http_methods(["GET", "POST"])
def commissions(request):
    if not can_sell(request.user):
        return _deny(request)

    manage = can_manage(request.user)
    salespeople = list(_egg_pos_salespeople()) if manage else [request.user]
    today = timezone.localdate()
    week_start = today - timedelta(days=today.weekday())
    requested_period = (request.GET.get("period") or "").strip().lower()

    selected_id = request.GET.get("salesperson") or request.POST.get("salesperson")
    selected = request.user
    if manage and salespeople:
        selected = salespeople[0]
        if selected_id:
            selected = get_object_or_404(User, pk=selected_id, is_active=True)

    if request.GET.get("start") or request.GET.get("end"):
        period_key = "custom"
        start_date = _parse_date(request.GET.get("start"), "Period start", default=week_start)
        end_date = _parse_date(request.GET.get("end"), "Period end", default=today)
    elif requested_period == "last_week":
        period_key = "last_week"
        end_date = week_start - timedelta(days=1)
        start_date = end_date - timedelta(days=6)
    elif requested_period == "this_month":
        period_key = "this_month"
        start_date = today.replace(day=1)
        end_date = today
    else:
        period_key = "this_week"
        start_date = week_start
        end_date = today

    # POST uses the explicit hidden dates from the form.
    if request.method == "POST":
        posted_start = request.POST.get("period_start")
        posted_end = request.POST.get("period_end")
        if posted_start:
            start_date = _parse_date(posted_start, "Period start", default=start_date)
        if posted_end:
            end_date = _parse_date(posted_end, "Period end", default=end_date)
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    if request.method == "POST":
        action = (request.POST.get("action") or "post").strip()
        try:
            if not manage:
                raise ValueError("Only Egg POS management can post or pay commissions.")
            if action == "post":
                salesperson = get_object_or_404(User, pk=request.POST.get("salesperson"), is_active=True)
                period_start = _parse_date(request.POST.get("period_start"), "Period start")
                period_end = _parse_date(request.POST.get("period_end"), "Period end")
                if period_end < period_start:
                    raise ValueError("Period end cannot be before period start.")
                overlap = EggPOSCommissionPeriod.objects.filter(
                    salesperson=salesperson,
                    period_start__lte=period_end,
                    period_end__gte=period_start,
                ).exists()
                if overlap:
                    raise ValueError("This salesperson already has a posted commission period overlapping these dates.")
                pending_expenses = EggPOSExpense.objects.filter(
                    used_by=salesperson,
                    status="pending",
                    affects_commission=True,
                    expense_date__gte=period_start,
                    expense_date__lte=period_end,
                ).count()
                if pending_expenses:
                    raise ValueError(
                        f"There are {pending_expenses} pending commission-affecting expense(s) in this period. "
                        "Approve or reject them before posting commission."
                    )
                snapshot = commission_preview(salesperson, period_start, period_end)
                if snapshot["commission_percent"] <= ZERO:
                    raise ValueError("Set this salesperson's Commission % in Staff Access first.")
                if snapshot["commission_amount"] <= ZERO:
                    raise ValueError("There is no positive commission to post for this period.")
                with transaction.atomic():
                    commission = EggPOSCommissionPeriod.objects.create(
                        salesperson=salesperson,
                        period_start=period_start,
                        period_end=period_end,
                        sales_revenue=snapshot["sales_revenue"],
                        cogs=snapshot["cogs"],
                        selling_expenses=snapshot["selling_expenses"],
                        commissionable_profit=snapshot["commissionable_profit"],
                        commission_percent=snapshot["commission_percent"],
                        commission_amount=snapshot["commission_amount"],
                        notes=(request.POST.get("notes") or "").strip(),
                        posted_by=request.user,
                    )
                    sync_commission_period(commission)
                messages.success(request, f"Commission Rs {commission.commission_amount:,.2f} posted for {salesperson.username}.")
                return redirect(f"{request.path}?salesperson={salesperson.id}&period=this_week")
            elif action == "recalculate":
                commission = get_object_or_404(
                    EggPOSCommissionPeriod.objects.select_related("salesperson").prefetch_related("payments"),
                    pk=request.POST.get("commission_id"),
                )
                if Decimal(commission.amount_paid) > ZERO:
                    raise ValueError("A paid or partially paid commission cannot be recalculated. Record an adjustment instead.")
                snapshot = commission_preview(commission.salesperson, commission.period_start, commission.period_end)
                commission.sales_revenue = snapshot["sales_revenue"]
                commission.cogs = snapshot["cogs"]
                commission.selling_expenses = snapshot["selling_expenses"]
                commission.commissionable_profit = snapshot["commissionable_profit"]
                commission.commission_percent = snapshot["commission_percent"]
                commission.commission_amount = snapshot["commission_amount"]
                commission.save(update_fields=[
                    "sales_revenue", "cogs", "selling_expenses", "commissionable_profit",
                    "commission_percent", "commission_amount",
                ])
                sync_commission_period(commission)
                messages.success(request, f"Commission period recalculated. New commission: Rs {commission.commission_amount:,.2f}.")
                return redirect(f"{request.path}?salesperson={commission.salesperson_id}&start={commission.period_start}&end={commission.period_end}")
            elif action == "pay":
                commission = get_object_or_404(EggPOSCommissionPeriod.objects.prefetch_related("payments"), pk=request.POST.get("commission_id"))
                balance = money(commission.balance_due)
                amount = _parse_money(request.POST.get("amount"), "Payment amount")
                if amount > balance:
                    raise ValueError(f"Payment cannot exceed commission balance Rs {balance:,.2f}.")
                method = (request.POST.get("payment_method") or "bank_transfer").strip()
                if method not in {value for value, _ in EggPOSCommissionPayment.PAYMENT_METHOD_CHOICES}:
                    raise ValueError("Select a valid payment method.")
                payment = EggPOSCommissionPayment.objects.create(
                    commission=commission,
                    payment_date=_parse_date(request.POST.get("payment_date"), "Payment date"),
                    amount=amount,
                    payment_method=method,
                    reference=(request.POST.get("reference") or "").strip(),
                    notes=(request.POST.get("notes") or "").strip(),
                    recorded_by=request.user,
                )
                sync_commission_payment(payment)
                messages.success(request, f"Commission payment Rs {amount:,.2f} recorded for {commission.salesperson.username}.")
                return redirect(f"{request.path}?salesperson={commission.salesperson_id}&period=this_week")
            else:
                raise ValueError("Invalid commission action.")
        except Exception as error:
            messages.error(request, str(error))

    preview = commission_preview(selected, start_date, end_date) if selected else None
    commission_summary = _commission_summary(selected, today) if selected else {"month_earned": ZERO, "month_received": ZERO, "balance_due": ZERO}
    month_start = today.replace(day=1)
    pending_expenses_count = 0
    overlap_periods = []
    if selected:
        pending_expenses_count = EggPOSExpense.objects.filter(
            used_by=selected,
            status="pending",
            affects_commission=True,
            expense_date__gte=start_date,
            expense_date__lte=end_date,
        ).count()
        overlap_periods = list(EggPOSCommissionPeriod.objects.filter(
            salesperson=selected,
            period_start__lte=end_date,
            period_end__gte=start_date,
        ).prefetch_related("payments").order_by("period_start"))

    periods = EggPOSCommissionPeriod.objects.select_related("salesperson", "posted_by").prefetch_related("payments")
    if selected:
        periods = periods.filter(salesperson=selected)
    elif not manage:
        periods = periods.filter(salesperson=request.user)
    periods = periods.order_by("-period_end", "-id")[:100]

    return render(request, "api/egg_pos_commissions.html", {
        "salespeople": salespeople,
        "selected": selected,
        "start_date": start_date,
        "end_date": end_date,
        "period_key": period_key,
        "preview": preview,
        "periods": periods,
        "overlap_periods": overlap_periods,
        "pending_expenses_count": pending_expenses_count,
        "commission_summary": commission_summary,
        "month_start": month_start,
        "payment_methods": EggPOSCommissionPayment.PAYMENT_METHOD_CHOICES,
        "today": today,
        "can_manage_pos": manage,
    })


@login_required
def owner_capital(request):
    if not can_manage(request.user):
        return _deny(request, "Only Egg POS management can manage owner capital.")
    ensure_default_accounts()

    if request.method == "POST":
        try:
            action = (request.POST.get("action") or "add").strip().lower()
            if action == "delete":
                capital = get_object_or_404(EggPOSOwnerCapitalTransaction, pk=request.POST.get("capital_id"))
                if capital.transaction_type == "owner_loan":
                    current_loan_balance = money(normal_account_balance("2040"))
                    if current_loan_balance - money(capital.amount) < ZERO:
                        raise ValueError("This owner loan cannot be removed because repayments already depend on it. Reverse or correct the repayment first.")
                with transaction.atomic():
                    JournalEntry.objects.filter(source_key=f"egg_pos:owner_capital:{capital.id}").delete()
                    label = capital.get_transaction_type_display()
                    amount = money(capital.amount)
                    capital.delete()
                messages.success(request, f"{label} Rs {amount:,.2f} removed and the books were updated.")
                return redirect("egg_pos_owner_capital")

            transaction_type = (request.POST.get("transaction_type") or "opening").strip()
            valid_types = {choice[0] for choice in EggPOSOwnerCapitalTransaction.TRANSACTION_TYPE_CHOICES}
            if transaction_type not in valid_types:
                raise ValueError("Choose a valid capital transaction type.")
            if transaction_type == "opening" and EggPOSOwnerCapitalTransaction.objects.filter(transaction_type="opening").exists():
                raise ValueError("Opening owner capital is already recorded. Use Additional Owner Investment, or delete/correct the opening entry first.")
            cash_account = (request.POST.get("cash_account") or "cash").strip()
            valid_accounts = {choice[0] for choice in EggPOSOwnerCapitalTransaction.CASH_ACCOUNT_CHOICES}
            if cash_account not in valid_accounts:
                raise ValueError("Choose Main Cash or Bank / Digital.")
            capital_date = _parse_date(request.POST.get("transaction_date"), "Transaction date")
            amount = _parse_money(request.POST.get("amount"), "Amount")
            if transaction_type == "loan_repayment":
                loan_balance_at_date = money(normal_account_balance("2040", as_of=capital_date))
                if amount > loan_balance_at_date:
                    raise ValueError(f"Loan repayment cannot exceed the owner loan balance of Rs {loan_balance_at_date:,.2f} on {capital_date:%d %b %Y}.")
            with transaction.atomic():
                capital = EggPOSOwnerCapitalTransaction.objects.create(
                    transaction_date=capital_date,
                    transaction_type=transaction_type,
                    amount=amount,
                    cash_account=cash_account,
                    reference=(request.POST.get("reference") or "").strip(),
                    notes=(request.POST.get("notes") or "").strip(),
                    recorded_by=request.user,
                )
                sync_owner_capital(capital)
            messages.success(request, f"{capital.get_transaction_type_display()} Rs {amount:,.2f} recorded.")
            return redirect("egg_pos_owner_capital")
        except Exception as error:
            messages.error(request, str(error))

    as_of = timezone.localdate()
    transactions = EggPOSOwnerCapitalTransaction.objects.select_related("recorded_by").all()
    suggested_date = as_of
    if not transactions.exists():
        suggested_date = JournalEntry.objects.filter(module="egg_pos").order_by("entry_date").values_list("entry_date", flat=True).first() or as_of
    has_opening = EggPOSOwnerCapitalTransaction.objects.filter(transaction_type="opening").exists()
    invested = money(EggPOSOwnerCapitalTransaction.objects.filter(
        transaction_type__in=["opening", "additional"]
    ).aggregate(total=Sum("amount"))["total"] or ZERO)
    withdrawals = money(EggPOSOwnerCapitalTransaction.objects.filter(
        transaction_type="withdrawal"
    ).aggregate(total=Sum("amount"))["total"] or ZERO)
    owner_loans_given = money(EggPOSOwnerCapitalTransaction.objects.filter(
        transaction_type="owner_loan"
    ).aggregate(total=Sum("amount"))["total"] or ZERO)
    owner_loans_repaid = money(EggPOSOwnerCapitalTransaction.objects.filter(
        transaction_type="loan_repayment"
    ).aggregate(total=Sum("amount"))["total"] or ZERO)
    net_capital = money(normal_account_balance("3000", as_of=as_of))
    owner_loan_balance = money(normal_account_balance("2040", as_of=as_of))
    return render(request, "api/egg_pos_owner_capital.html", {
        "transactions": transactions,
        "invested": invested,
        "withdrawals": withdrawals,
        "net_capital": net_capital,
        "owner_loans_given": owner_loans_given,
        "owner_loans_repaid": owner_loans_repaid,
        "owner_loan_balance": owner_loan_balance,
        "main_cash": money(normal_account_balance("1000", as_of=as_of)),
        "bank": money(normal_account_balance("1010", as_of=as_of)),
        "today": as_of,
        "suggested_date": suggested_date,
        "has_opening": has_opening,
        "transaction_types": EggPOSOwnerCapitalTransaction.TRANSACTION_TYPE_CHOICES,
        "cash_accounts": EggPOSOwnerCapitalTransaction.CASH_ACCOUNT_CHOICES,
    })


@login_required
def accounting_overview(request):
    if not can_manage(request.user):
        return _deny(request, "Only Egg POS management can view accounting reports.")
    ensure_default_accounts()
    start_date, end_date = _report_range(request)
    statement = build_income_statement(start_date, end_date)
    as_of = end_date
    summary = {
        "cash": money(normal_account_balance("1000", as_of=as_of)),
        "bank": money(normal_account_balance("1010", as_of=as_of)),
        "staff_cash": money(normal_account_balance("1040", as_of=as_of)),
        "receivables": money(normal_account_balance("1100", as_of=as_of)),
        "inventory": money(normal_account_balance("1200", as_of=as_of)),
        "supplier_payable": money(normal_account_balance("2000", as_of=as_of)),
        "farm_payable": money(normal_account_balance("2010", as_of=as_of)),
        "staff_reimbursements": money(normal_account_balance("2020", as_of=as_of)),
        "commission_payable": money(normal_account_balance("2030", as_of=as_of)),
        "owner_loan_payable": money(normal_account_balance("2040", as_of=as_of)),
        "owner_capital": money(normal_account_balance("3000", as_of=as_of)),
    }

    # Purchase-readiness intentionally excludes money still with sales staff/customers.
    # Owner loans are liabilities, but they are temporary funding provided specifically so
    # the business can use the cash. They therefore remain visible on the Balance Sheet
    # and Owner Position, but are not reserved from day-to-day purchase capacity unless
    # management actually repays them.
    immediate_payables = money(
        max(summary["supplier_payable"], ZERO)
        + max(summary["farm_payable"], ZERO)
        + max(summary["staff_reimbursements"], ZERO)
        + max(summary["commission_payable"], ZERO)
    )
    available_now = money(summary["cash"] + summary["bank"] - immediate_payables)
    after_staff_handover = money(available_now + summary["staff_cash"])
    after_customer_collection = money(after_staff_handover + summary["receivables"])
    cash_after_owner_loan_repayment = money(available_now - max(summary["owner_loan_payable"], ZERO))
    purchase_readiness = {
        "committed_payables": immediate_payables,
        "available_now": available_now,
        "after_staff_handover": after_staff_handover,
        "after_customer_collection": after_customer_collection,
        "owner_loan_outstanding": max(summary["owner_loan_payable"], ZERO),
        "cash_after_owner_loan_repayment": cash_after_owner_loan_repayment,
    }

    # Reconcile customer collections by custody so management can see where sales cash is.
    payment_lines = JournalLine.objects.filter(
        entry__module="egg_pos",
        entry__source_type="egg_pos_sale_payment",
        entry__entry_date__lte=as_of,
        account__code__in=["1000", "1010", "1040"],
    )
    def debit_for(code):
        return money(payment_lines.filter(account__code=code).aggregate(total=Sum("debit"))["total"] or ZERO)

    direct_cash = debit_for("1000")
    direct_bank = debit_for("1010")
    staff_gross_collections = debit_for("1040")
    staff_expenses_from_collection = money(JournalLine.objects.filter(
        entry__module="egg_pos",
        entry__source_type="egg_pos_expense",
        entry__entry_date__lte=as_of,
        account__code="1040",
    ).aggregate(total=Sum("credit"))["total"] or ZERO)
    staff_handovers = money(JournalLine.objects.filter(
        entry__module="egg_pos",
        entry__source_type="egg_pos_cash_settlement",
        entry__entry_date__lte=as_of,
        account__code="1040",
    ).aggregate(total=Sum("credit"))["total"] or ZERO)
    total_sales_to_date = money(normal_account_balance("4000", as_of=as_of))
    total_collected = money(direct_cash + direct_bank + staff_gross_collections)
    staff_accountable = money(staff_gross_collections - staff_expenses_from_collection)
    cash_reconciliation = {
        "sales": total_sales_to_date,
        "receivables": summary["receivables"],
        "collected": total_collected,
        "direct_cash": direct_cash,
        "direct_bank": direct_bank,
        "direct_company": money(direct_cash + direct_bank),
        "staff_gross_collections": staff_gross_collections,
        "staff_expenses": staff_expenses_from_collection,
        "staff_accountable": staff_accountable,
        "staff_handovers": staff_handovers,
        "staff_outstanding": summary["staff_cash"],
    }

    first_entry_date = JournalEntry.objects.filter(
        module="egg_pos", entry_date__lte=as_of
    ).order_by("entry_date").values_list("entry_date", flat=True).first() or as_of
    cumulative_statement = build_income_statement(first_entry_date, as_of)
    current_equity = money(summary["owner_capital"] + cumulative_statement["net_profit"])

    recent_entries = JournalEntry.objects.filter(module="egg_pos", entry_date__lte=as_of).prefetch_related("lines__account").order_by("-entry_date", "-id")[:20]
    return render(request, "api/egg_pos_accounting.html", {
        "start_date": start_date,
        "end_date": end_date,
        "statement": statement,
        "summary": summary,
        "purchase_readiness": purchase_readiness,
        "cash_reconciliation": cash_reconciliation,
        "cumulative_net_profit": cumulative_statement["net_profit"],
        "current_equity": current_equity,
        "capital_missing": summary["owner_capital"] == ZERO,
        "recent_entries": recent_entries,
    })


@login_required
def general_ledger(request):
    if not can_manage(request.user):
        return _deny(request, "Only Egg POS management can view the general ledger.")
    ensure_default_accounts()
    start_date, end_date = _report_range(request)
    accounts = list(ChartOfAccount.objects.filter(is_active=True).order_by("code"))
    selected_code = (request.GET.get("account") or (accounts[0].code if accounts else "1000")).strip()
    account = get_object_or_404(ChartOfAccount, code=selected_code)

    opening_qs = JournalLine.objects.filter(account=account, entry__module="egg_pos", entry__entry_date__lt=start_date)
    opening_totals = opening_qs.aggregate(debit=Sum("debit"), credit=Sum("credit"))
    raw_running = money(Decimal(opening_totals["debit"] or 0) - Decimal(opening_totals["credit"] or 0))
    opening_balance = raw_running if account.normal_balance == "debit" else money(-raw_running)

    rows = []
    for journal_line in account_activity(account, start_date=start_date, end_date=end_date):
        raw_running = money(raw_running + Decimal(journal_line.debit or 0) - Decimal(journal_line.credit or 0))
        display_balance = raw_running if account.normal_balance == "debit" else money(-raw_running)
        rows.append({"line": journal_line, "balance": display_balance})

    return render(request, "api/egg_pos_general_ledger.html", {
        "accounts": accounts,
        "account": account,
        "rows": rows,
        "opening_balance": opening_balance,
        "closing_balance": rows[-1]["balance"] if rows else opening_balance,
        "start_date": start_date,
        "end_date": end_date,
    })


@login_required
def cashbook(request):
    if not can_manage(request.user):
        return _deny(request, "Only Egg POS management can view the cashbook.")

    ensure_default_accounts()
    start_date, end_date = _report_range(request)
    account_filter = (request.GET.get("account") or "all").strip().lower()
    if account_filter == "cash":
        account_codes = ["1000"]
    elif account_filter == "bank":
        account_codes = ["1010"]
    else:
        account_filter = "all"
        account_codes = ["1000", "1010"]

    opening_totals = JournalLine.objects.filter(
        account__code__in=account_codes,
        entry__module="egg_pos",
        entry__entry_date__lt=start_date,
    ).aggregate(debit=Sum("debit"), credit=Sum("credit"))
    opening_balance = money(Decimal(opening_totals["debit"] or 0) - Decimal(opening_totals["credit"] or 0))

    running = opening_balance
    rows = []
    inflow_total = ZERO
    outflow_total = ZERO
    lines = JournalLine.objects.filter(
        account__code__in=account_codes,
        entry__module="egg_pos",
        entry__entry_date__gte=start_date,
        entry__entry_date__lte=end_date,
    ).select_related("entry", "account").order_by("entry__entry_date", "entry_id", "id")
    for journal_line in lines:
        inflow = money(journal_line.debit)
        outflow = money(journal_line.credit)
        inflow_total = money(inflow_total + inflow)
        outflow_total = money(outflow_total + outflow)
        running = money(running + inflow - outflow)
        rows.append({
            "line": journal_line,
            "inflow": inflow,
            "outflow": outflow,
            "balance": running,
        })

    return render(request, "api/egg_pos_cashbook.html", {
        "rows": list(reversed(rows)),
        "opening_balance": opening_balance,
        "closing_balance": running,
        "inflow_total": inflow_total,
        "outflow_total": outflow_total,
        "start_date": start_date,
        "end_date": end_date,
        "period_key": _report_period_key(request, start_date, end_date),
        "account_filter": account_filter,
    })


@login_required
def trial_balance(request):
    if not can_manage(request.user):
        return _deny(request, "Only Egg POS management can view the trial balance.")
    as_of = _parse_date(request.GET.get("as_of"), "As of date", default=timezone.localdate())
    rows, total_debit, total_credit = build_trial_balance(as_of)
    return render(request, "api/egg_pos_trial_balance.html", {
        "as_of": as_of,
        "rows": rows,
        "total_debit": total_debit,
        "total_credit": total_credit,
        "balanced": total_debit == total_credit,
    })


@login_required
def income_statement(request):
    if not can_manage(request.user):
        return _deny(request, "Only Egg POS management can view the income statement.")
    start_date, end_date = _report_range(request)
    statement = build_income_statement(start_date, end_date)
    return render(request, "api/egg_pos_income_statement.html", {
        "start_date": start_date,
        "end_date": end_date,
        "period_key": _report_period_key(request, start_date, end_date),
        "statement": statement,
    })


@login_required
def party_ledger(request, party_type, party_id):
    manage = can_manage(request.user)
    if not manage and not (party_type == "staff" and int(party_id) == request.user.id):
        return _deny(request, "You do not have access to this ledger.")

    start_date, end_date = _report_range(request)
    context = {"start_date": start_date, "end_date": end_date, "party_type": party_type}

    if party_type == "supplier":
        if not manage:
            return _deny(request)
        party = get_object_or_404(EggPOSSupplier, pk=party_id)
        lines = party_activity(party_type="supplier", party_id=party.id, start_date=start_date, end_date=end_date, account_codes=["2000"])
        context.update({"party": party, "party_label": party.name, "balance": money(gl_supplier_balance(party, as_of=end_date)), "lines": lines, "balance_label": "Amount Payable"})
    elif party_type == "customer":
        if not manage:
            return _deny(request)
        party = get_object_or_404(EggPOSCustomer, pk=party_id)
        lines = party_activity(party_type="customer", party_id=party.id, start_date=start_date, end_date=end_date, account_codes=["1100"])
        context.update({"party": party, "party_label": party.display_label, "balance": money(gl_customer_balance(party, as_of=end_date)), "lines": lines, "balance_label": "Amount Receivable"})
    elif party_type == "staff":
        party = get_object_or_404(User, pk=party_id)
        lines = party_activity(party_type="user", party_id=party.id, start_date=start_date, end_date=end_date)
        context.update({
            "party": party,
            "party_label": party.get_full_name().strip() or party.username,
            "lines": lines,
            "balance_label": "Cash Held",
            "balance": money(staff_cash_balance(party, as_of=end_date)),
            "reimbursement_balance": money(staff_reimbursement_balance(party, as_of=end_date)),
            "commission_balance": money(staff_commission_balance(party, as_of=end_date)),
        })
    else:
        return _deny(request, "Unknown ledger type.")

    return render(request, "api/egg_pos_party_ledger.html", context)


def _farm_transfer_is_editable(transfer):
    if transfer.is_voided or Decimal(transfer.amount_paid or 0) > ZERO:
        return False, "Only an unpaid, active transfer can be edited or voided."
    for item in transfer.items.select_related("lot").all():
        if item.lot_id and int(item.lot.quantity_remaining or 0) != int(item.lot.quantity_received or 0):
            return False, "This transfer stock has already been used in a sale, so it cannot be edited or voided. Reverse the related sale first."
    return True, ""


@login_required
@require_http_methods(["GET", "POST"])
def edit_farm_transfer(request, transfer_id):
    if not can_manage(request.user):
        return _deny(request, "Only Egg POS management can correct internal transfers.")
    transfer = get_object_or_404(EggPOSFarmTransfer.objects.prefetch_related("items__product", "items__lot", "payments"), pk=transfer_id)
    editable, reason = _farm_transfer_is_editable(transfer)
    if not editable:
        messages.error(request, reason)
        return redirect("egg_pos_farm_transfer_detail", transfer_id=transfer.id)

    batches = list(Batch.objects.filter(shed__shed_type="layer", is_active=True, status="active").select_related("shed").order_by("-start_date", "-id"))
    products = EggPOSProduct.objects.filter(is_active=True).order_by("name")
    old_qty = transfer.total_quantity
    for batch in batches:
        batch.pos_stock = farm_egg_stock(batch)
        if batch.id == transfer.batch_id:
            batch.pos_stock["available_for_edit"] = batch.pos_stock["available"] + old_qty
        else:
            batch.pos_stock["available_for_edit"] = batch.pos_stock["available"]

    if request.method == "POST":
        try:
            batch = get_object_or_404(Batch.objects.select_related("shed"), pk=request.POST.get("batch"), shed__shed_type="layer", is_active=True, status="active")
            transfer_date = _parse_date(request.POST.get("transfer_date"), "Transfer date")
            due_raw = (request.POST.get("payment_due_date") or "").strip()
            due_date = _parse_date(due_raw, "Payment due date") if due_raw else None
            if due_date and due_date < transfer_date:
                raise ValueError("Payment due date cannot be before the transfer date.")
            transport_cost = _parse_money(request.POST.get("transport_cost") or "0", "Transport cost", allow_zero=True)
            transport_method = (request.POST.get("transport_payment_method") or "cash").strip()
            if transport_method not in {v for v, _ in EggPOSFarmTransfer.TRANSPORT_PAYMENT_CHOICES}:
                raise ValueError("Select a valid transport payment source.")

            rows=[]; total_qty=0
            pids=request.POST.getlist("product_id"); qtys=request.POST.getlist("quantity"); costs=request.POST.getlist("unit_cost"); totals=request.POST.getlist("line_total_cost")
            for i,pid in enumerate(pids):
                if not pid: continue
                product=get_object_or_404(EggPOSProduct, pk=pid, is_active=True)
                qty=_parse_positive_int(qtys[i] if i < len(qtys) else None, "Quantity")
                traw=(totals[i] if i < len(totals) else "") or ""; uraw=(costs[i] if i < len(costs) else "") or ""
                if str(traw).strip():
                    line_cost=_parse_money(traw,"Line transfer cost"); unit=(Decimal(line_cost)/Decimal(qty)).quantize(UNIT_COST_QUANT)
                else: unit=_parse_unit_cost(uraw,"Farm transfer rate")
                rows.append((product,qty,unit)); total_qty += qty
            if not rows: raise ValueError("Add at least one product grading row.")
            available=farm_egg_stock(batch)["available"] + (old_qty if batch.id == transfer.batch_id else 0)
            if total_qty > available: raise ValueError(f"Only {available} farm eggs are available for this correction; {total_qty} requested.")
            transport_per=(Decimal(transport_cost)/Decimal(total_qty)).quantize(UNIT_COST_QUANT) if total_qty and transport_cost > ZERO else Decimal("0.000000")

            with transaction.atomic():
                for item in list(transfer.items.select_related("lot").all()):
                    if item.lot_id: item.lot.delete()
                transfer.items.all().delete()
                transfer.batch=batch; transfer.transfer_date=transfer_date; transfer.payment_due_date=due_date; transfer.transport_cost=transport_cost; transfer.transport_payment_method=transport_method; transfer.notes=(request.POST.get("notes") or "").strip()
                transfer.save(update_fields=["batch","transfer_date","payment_due_date","transport_cost","transport_payment_method","notes"])
                for number,(product,qty,unit) in enumerate(rows,start=1):
                    item=EggPOSFarmTransferItem.objects.create(transfer=transfer,product=product,quantity=qty,unit_cost=unit)
                    landed=(Decimal(unit)+transport_per).quantize(UNIT_COST_QUANT)
                    lot=EggPOSInventoryLot.objects.create(product=product,source_type="farm",farm_batch=batch,farm_transfer=transfer,lot_code=make_lot_code("RN",transfer.id,number),received_date=transfer_date,quantity_received=qty,quantity_remaining=qty,unit_cost=landed,notes=f"RayNoor corrected transfer {transfer.transfer_number} | Base Rs {unit:.4f}/egg + transport Rs {transport_per:.4f}/egg")
                    item.lot=lot; item.save(update_fields=["lot"])
                sync_farm_transfer(transfer)
            messages.success(request, f"{transfer.transfer_number} corrected successfully. Stock, farm payable and accounting were updated together.")
            return redirect("egg_pos_farm_transfer_detail", transfer_id=transfer.id)
        except Exception as error: messages.error(request, str(error))

    return render(request,"api/egg_pos_farm_transfer_edit.html",{"transfer":transfer,"batches":batches,"products":products,"transport_methods":EggPOSFarmTransfer.TRANSPORT_PAYMENT_CHOICES})


@login_required
@require_POST
def void_farm_transfer(request, transfer_id):
    if not can_manage(request.user): return _deny(request, "Only Egg POS management can void internal transfers.")
    transfer=get_object_or_404(EggPOSFarmTransfer.objects.prefetch_related("items__lot","payments"),pk=transfer_id)
    editable, reason=_farm_transfer_is_editable(transfer)
    if not editable:
        messages.error(request, reason); return redirect("egg_pos_farm_transfer_detail",transfer_id=transfer.id)
    reason_text=(request.POST.get("reason") or "Entry made in error").strip()[:255]
    with transaction.atomic():
        for item in list(transfer.items.select_related("lot").all()):
            if item.lot_id: item.lot.delete()
        JournalEntry.objects.filter(source_key=f"egg_pos:farm_transfer:{transfer.id}").delete()
        transfer.is_voided=True; transfer.void_reason=reason_text; transfer.voided_at=timezone.now(); transfer.voided_by=request.user
        transfer.save(update_fields=["is_voided","void_reason","voided_at","voided_by"])
    messages.success(request,f"{transfer.transfer_number} voided. Its POS stock, farm payable and accounting effect were removed.")
    return redirect("egg_pos_farm_transfer_detail",transfer_id=transfer.id)
