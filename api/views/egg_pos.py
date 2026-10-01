from datetime import date
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
)
from api.models.sensor import Batch
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
            .select_related("supplier", "farm_batch")
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
        rows.append({
            "product": product,
            "stock": current,
            "expired_stock": expired,
            "purchase_stock": purchased,
            "farm_stock": farm,
            "stock_value": money(value),
            "low_stock": current <= int(product.low_stock_eggs or 0),
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
    all_sales = list(customer.sales.all())
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

    sales_qs = EggPOSSale.objects.filter(sale_date=today)
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

    recent_sales = EggPOSSale.objects.select_related("created_by")
    if not can_manage(request.user):
        recent_sales = recent_sales.filter(created_by=request.user)
    recent_sales = recent_sales.order_by("-sale_date", "-id")[:10]

    farm_transfer_payable = ZERO
    farm_transfer_total = ZERO
    farm_transfer_paid = ZERO
    if can_manage(request.user):
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
        "can_manage_pos": can_manage(request.user),
    })


@login_required
def inventory(request):
    if not can_sell(request.user):
        return _deny(request)
    products = EggPOSProduct.objects.filter(is_active=True).order_by("name")
    return render(request, "api/egg_pos_inventory.html", {
        "stock_rows": _stock_rows(products),
        "can_manage_pos": can_manage(request.user),
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
    return render(request, "api/egg_pos_purchases.html", {
        "purchases": EggPOSPurchase.objects.select_related("supplier", "created_by").prefetch_related("items__product")[:100],
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
            product_ids = request.POST.getlist("product_id")
            quantities = request.POST.getlist("quantity")
            costs = request.POST.getlist("unit_cost")
            expiries = request.POST.getlist("expiry_date")

            rows = []
            for index, product_id in enumerate(product_ids):
                if not product_id:
                    continue
                product = get_object_or_404(EggPOSProduct, pk=product_id, is_active=True)
                qty = _parse_positive_int(quantities[index] if index < len(quantities) else None, "Quantity")
                unit_cost = _parse_money(costs[index] if index < len(costs) else None, "Unit cost")
                expiry = _parse_date(expiries[index], "Expiry date", default=None) if index < len(expiries) and expiries[index] else None
                if expiry and expiry < purchase_date:
                    raise ValueError("Expiry date cannot be before purchase date.")
                rows.append((product, qty, unit_cost, expiry))
            if not rows:
                raise ValueError("Add at least one purchase item.")

            with transaction.atomic():
                purchase = EggPOSPurchase.objects.create(
                    supplier=supplier,
                    purchase_date=purchase_date,
                    reference=(request.POST.get("reference") or "").strip(),
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
                        unit_cost=unit_cost,
                        notes=f"Purchase #{purchase.id} {purchase.reference}".strip(),
                    )
                    item.lot = lot
                    item.save(update_fields=["lot"])

            messages.success(request, f"Purchase #{purchase.id} saved and inventory updated.")
            return redirect("egg_pos_purchase_list")
        except Exception as error:
            messages.error(request, str(error))

    return render(request, "api/egg_pos_purchase_add.html", {
        "products": products_qs,
        "suppliers": suppliers_qs,
        "today": timezone.localdate(),
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

            product_ids = request.POST.getlist("product_id")
            quantities = request.POST.getlist("quantity")
            costs = request.POST.getlist("unit_cost")

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
                unit_cost = _parse_money(
                    costs[index] if index < len(costs) else None,
                    "Farm transfer rate",
                )
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

            with transaction.atomic():
                transfer = EggPOSFarmTransfer.objects.create(
                    batch=batch,
                    transfer_date=transfer_date,
                    payment_due_date=payment_due_date,
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
                    lot = EggPOSInventoryLot.objects.create(
                        product=product,
                        source_type="farm",
                        farm_batch=batch,
                        farm_transfer=transfer,
                        lot_code=make_lot_code("RN", transfer.id, number),
                        received_date=transfer_date,
                        quantity_received=qty,
                        quantity_remaining=qty,
                        unit_cost=unit_cost,
                        notes=f"RayNoor Egg Production internal transfer {transfer.transfer_number}",
                    )
                    item.lot = lot
                    item.save(update_fields=["lot"])

            messages.success(
                request,
                f"{transfer.transfer_number} created: {total_qty} eggs, "
                f"Rs {money(total_value):,.2f} payable to RayNoor Egg Production.",
            )
            return redirect("egg_pos_farm_transfer_detail", transfer_id=transfer.id)
        except Exception as error:
            messages.error(request, str(error))

    all_transfers = list(
        EggPOSFarmTransfer.objects
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
    return render(request, "api/egg_pos_farm_transfer_detail.html", {
        "transfer": transfer,
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
        EggPOSFarmTransferPayment.objects.create(
            transfer=transfer,
            payment_date=payment_date,
            amount=amount,
            payment_method=method,
            reference=(request.POST.get("reference") or "").strip(),
            notes=(request.POST.get("notes") or "").strip(),
            recorded_by=request.user,
        )
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
            rows, seen_products = [], set()

            for index, product_id in enumerate(product_ids):
                if not product_id:
                    continue

                product = get_object_or_404(EggPOSProduct, pk=product_id, is_active=True)
                if product.id in seen_products:
                    raise ValueError(f"{product.name} is listed more than once. Combine it into one row.")

                seen_products.add(product.id)
                qty = _parse_positive_int(
                    quantities[index] if index < len(quantities) else None,
                    "Quantity",
                )
                unit_price = _parse_money(
                    prices[index] if index < len(prices) else None,
                    "Sale price",
                )

                # Fast pre-check for a friendly error before the transaction.
                stock = available_product_stock(product, sale_date)
                if stock <= 0:
                    raise ValueError(f"{product.name} is out of stock. Sale was not created.")
                if qty > stock:
                    raise ValueError(
                        f"Only {stock} {product.name} eggs are available for this sale date."
                    )

                rows.append((product, qty, unit_price))

            if not rows:
                raise ValueError("Add at least one sale item.")

            subtotal = money(sum((Decimal(qty) * price for _, qty, price in rows), ZERO))
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
                for product, qty, _unit_price in rows:
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
                            f"Only {locked_available} {product.name} eggs are available now. "
                            "Stock changed before checkout, so the sale was not created."
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
                for product, qty, unit_price in rows:
                    item = EggPOSSaleItem.objects.create(
                        sale=sale,
                        product=product,
                        quantity=qty,
                        unit_price=unit_price,
                        line_total=money(Decimal(qty) * unit_price),
                    )
                    cogs_total += allocate_fifo_to_sale_item(item, sale.sale_date)

                sale.cogs_total = money(cogs_total)
                sale.profit_total = money(sale.net_total - sale.cogs_total)
                sale.save(update_fields=["cogs_total", "profit_total"])

                if amount_received > ZERO:
                    EggPOSSalePayment.objects.create(
                        sale=sale,
                        payment_date=sale.sale_date,
                        amount=amount_received,
                        payment_method=payment_method,
                        notes="Payment received at checkout.",
                        recorded_by=request.user,
                    )

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
    all_sales = list(customer.sales.all())
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
        EggPOSSalePayment.objects.create(sale=sale,payment_date=payment_date,amount=amount,payment_method=method,reference=(request.POST.get("reference") or "").strip(),notes=(request.POST.get("notes") or "").strip(),recorded_by=request.user)
        messages.success(request,f"Payment Rs {amount:,.2f} recorded for {sale.sale_number}.")
    except Exception as error:
        messages.error(request,str(error))
    return redirect("egg_pos_sale_detail",sale_id=sale.id)


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

            # Selecting any permission means this user must have active
            # Egg POS access. To fully disable access, Admin simply clears
            # Active, Can Sell and Can Manage.
            access.can_sell = requested_sell
            access.can_manage = requested_manage
            access.is_active = requested_active or requested_sell or requested_manage
            access.save()
            messages.success(request, f"Egg POS access updated for {user.username}.")
            return redirect("egg_pos_staff_access")
        except Exception as error:
            messages.error(request, str(error))

    users = User.objects.filter(is_active=True).order_by("username")
    access_map = {row.user_id: row for row in EggPOSUserAccess.objects.select_related("user")}
    rows = [{"user": user, "access": access_map.get(user.id)} for user in users]
    return render(request, "api/egg_pos_staff_access.html", {"rows": rows})
