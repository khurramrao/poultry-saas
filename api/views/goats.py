from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST, require_http_methods

from api.models.goats import (
    Goat,
    GoatAccountPayment,
    GoatCostAllocation,
    GoatCostEntry,
    GoatSale,
    GoatWeightRecord,
)
from api.models.sensor import Device, SensorData, Shed


ZERO = Decimal("0.00")
MONEY = Decimal("0.01")
ACCOUNT_TOLERANCE = Decimal("0.50")


def _money(value):
    if value is None:
        return ZERO
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def _is_admin(user):
    return bool(user.is_superuser or user.is_staff)


def _can_view_goats(user):
    return _is_admin(user) or hasattr(user, "investor_profile")


def _visible_goats(user):
    qs = (
        Goat.objects
        .select_related("owner", "shed")
        .prefetch_related("weight_records", "cost_allocations__cost_entry")
    )

    if _is_admin(user):
        return qs

    if hasattr(user, "investor_profile"):
        return qs.filter(owner=user)

    return qs.none()


def _goat_owner_choices():
    return (
        User.objects
        .filter(
            Q(is_superuser=True)
            | Q(is_staff=True)
            | Q(investor_profile__isnull=False)
        )
        .distinct()
        .order_by("username")
    )


def _goat_sheds():
    return Shed.objects.filter(shed_type="goat").order_by("name", "id")


def _goat_category_totals(goat):
    totals = {
        "feed": ZERO,
        "medicine": ZERO,
        "expense": ZERO,
    }

    allocations = (
        GoatCostAllocation.objects
        .filter(goat=goat)
        .values("cost_entry__category")
        .annotate(total=Sum("amount"))
    )

    for row in allocations:
        category = row["cost_entry__category"]
        if category in totals:
            totals[category] = _money(row["total"])

    return totals


def goat_finance_snapshot(goat):
    categories = _goat_category_totals(goat)
    purchase_cost = _money(goat.purchase_cost)
    operating_cost = _money(
        categories["feed"]
        + categories["medicine"]
        + categories["expense"]
    )
    total_cost = _money(purchase_cost + operating_cost)
    current_weight = _money(goat.current_weight_kg or ZERO)

    cost_per_kg = ZERO
    if current_weight > ZERO:
        cost_per_kg = _money(total_cost / current_weight)

    sale = None
    try:
        sale = goat.sale_record
    except GoatSale.DoesNotExist:
        pass

    sale_revenue = _money(sale.total_amount) if sale else ZERO
    sale_profit = _money(sale.profit) if sale else ZERO
    sale_roi = _money(sale.roi) if sale else ZERO

    return {
        "goat": goat,
        "purchase_cost": purchase_cost,
        "feed_cost": categories["feed"],
        "medicine_cost": categories["medicine"],
        "expense_cost": categories["expense"],
        "operating_cost": operating_cost,
        "total_cost": total_cost,
        "current_weight": current_weight,
        "cost_per_kg": cost_per_kg,
        "sale": sale,
        "sale_revenue": sale_revenue,
        "sale_profit": sale_profit,
        "sale_roi": sale_roi,
    }


def _owner_goat_account_snapshot(owner):
    goats = list(
        Goat.objects
        .filter(owner=owner)
        .select_related("owner", "shed")
        .order_by("goat_code", "id")
    )
    goat_rows = [goat_finance_snapshot(goat) for goat in goats]

    total_cost = _money(sum((row["total_cost"] for row in goat_rows), ZERO))
    total_purchase = _money(sum((row["purchase_cost"] for row in goat_rows), ZERO))
    total_feed = _money(sum((row["feed_cost"] for row in goat_rows), ZERO))
    total_medicine = _money(sum((row["medicine_cost"] for row in goat_rows), ZERO))
    total_expense = _money(sum((row["expense_cost"] for row in goat_rows), ZERO))

    total_paid = _money(
        owner.goat_account_payments.aggregate(total=Sum("amount"))["total"]
        or ZERO
    )

    balance = _money(total_cost - total_paid)
    if abs(balance) < ACCOUNT_TOLERANCE:
        balance = ZERO

    outstanding = max(balance, ZERO)
    credit = max(-balance, ZERO)

    if credit > ZERO:
        status = "credit"
        status_label = "Credit"
    elif outstanding > ZERO:
        status = "outstanding"
        status_label = "Outstanding"
    elif total_cost > ZERO:
        status = "paid"
        status_label = "Paid"
    else:
        status = "no_cost"
        status_label = "No Cost"

    total_sales = _money(sum((row["sale_revenue"] for row in goat_rows), ZERO))
    total_profit = _money(sum((row["sale_profit"] for row in goat_rows if row["sale"]), ZERO))

    owner_name = owner.get_full_name().strip() or owner.username

    return {
        "owner": owner,
        "owner_name": owner_name,
        "goats": goat_rows,
        "goat_count": len(goats),
        "active_goat_count": sum(1 for goat in goats if goat.status == "active"),
        "total_purchase": total_purchase,
        "total_feed": total_feed,
        "total_medicine": total_medicine,
        "total_expense": total_expense,
        "total_cost": total_cost,
        "total_paid": total_paid,
        "outstanding": outstanding,
        "credit": credit,
        "status": status,
        "status_label": status_label,
        "total_sales": total_sales,
        "total_profit": total_profit,
    }


def _build_owner_statement(owner):
    rows = []

    for goat in Goat.objects.filter(owner=owner).order_by("purchase_date", "id"):
        if goat.purchase_cost and goat.purchase_cost > ZERO:
            rows.append({
                "date": goat.purchase_date,
                "sort_type": 0,
                "sort_id": 100000 + goat.id,
                "entry_type": "charge",
                "category": "Goat Purchase",
                "description": f"{goat.goat_code} · {goat.name or goat.breed or 'Goat'}",
                "goat": goat,
                "charge": _money(goat.purchase_cost),
                "payment": ZERO,
            })

    for allocation in (
        GoatCostAllocation.objects
        .filter(owner_snapshot=owner)
        .select_related("goat", "cost_entry")
        .order_by("cost_entry__entry_date", "id")
    ):
        entry = allocation.cost_entry
        description = f"{allocation.goat.goat_code} · {entry.display_title}"
        if entry.notes:
            description += f" — {entry.notes}"

        rows.append({
            "date": entry.entry_date,
            "sort_type": 0,
            "sort_id": 200000 + allocation.id,
            "entry_type": "charge",
            "category": entry.get_category_display(),
            "description": description,
            "goat": allocation.goat,
            "charge": _money(allocation.amount),
            "payment": ZERO,
        })

    for payment in owner.goat_account_payments.all().order_by("payment_date", "id"):
        description = payment.get_payment_method_display()
        if payment.reference:
            description += f" · {payment.reference}"
        if payment.notes:
            description += f" — {payment.notes}"

        rows.append({
            "date": payment.payment_date,
            "sort_type": 1,
            "sort_id": 300000 + payment.id,
            "entry_type": "payment",
            "category": "Payment Received",
            "description": description,
            "goat": None,
            "charge": ZERO,
            "payment": _money(payment.amount),
        })

    rows.sort(key=lambda row: (row["date"], row["sort_type"], row["sort_id"]))

    running_balance = ZERO
    for row in rows:
        running_balance = _money(
            running_balance + row["charge"] - row["payment"]
        )
        if abs(running_balance) < ACCOUNT_TOLERANCE:
            running_balance = ZERO
        row["running_balance"] = running_balance

    return rows


def _allocate_exact_amount(total_amount, goats):
    goats = list(goats)
    if not goats:
        return []

    total_amount = _money(total_amount)
    total_paisa = int((total_amount * 100).to_integral_value(rounding=ROUND_HALF_UP))
    base_paisa, remainder = divmod(total_paisa, len(goats))

    allocations = []
    for index, goat in enumerate(goats):
        paisa = base_paisa + (1 if index < remainder else 0)
        allocations.append((goat, Decimal(paisa) / Decimal("100")))

    return allocations


def _goat_sensor_snapshot(shed):
    if shed is None:
        return None

    device = Device.objects.filter(shed=shed, is_active=True).order_by("id").first()
    if not device:
        return {
            "device": None,
            "latest": None,
            "offline": True,
            "last_update": "No device assigned",
        }

    latest = SensorData.objects.filter(device=device).order_by("-created_at").first()
    offline = True
    if latest:
        offline = timezone.now() - latest.created_at > timedelta(minutes=3)

    return {
        "device": device,
        "latest": latest,
        "offline": offline,
        "last_update": latest.created_at if latest else None,
    }


@login_required
def goat_dashboard(request):
    if not _can_view_goats(request.user):
        messages.error(request, "You do not have permission to view Goat Farm.")
        return redirect("dashboard")

    is_admin = _is_admin(request.user)
    goats = list(_visible_goats(request.user).order_by("status", "goat_code", "id"))
    rows = [goat_finance_snapshot(goat) for goat in goats]

    active_rows = [row for row in rows if row["goat"].status == "active"]
    active_males = [row for row in active_rows if row["goat"].sex == "male"]
    active_females = [row for row in active_rows if row["goat"].sex == "female"]

    total_purchase_cost = _money(sum((row["purchase_cost"] for row in rows), ZERO))
    total_operating_cost = _money(sum((row["operating_cost"] for row in rows), ZERO))
    total_goat_cost = _money(sum((row["total_cost"] for row in rows), ZERO))
    total_live_weight = _money(sum((row["current_weight"] for row in active_rows), ZERO))
    owner_count = len({row["goat"].owner_id for row in active_rows})

    aggregate_cost_per_kg = ZERO
    active_cost = _money(sum((row["total_cost"] for row in active_rows), ZERO))
    if total_live_weight > ZERO:
        aggregate_cost_per_kg = _money(active_cost / total_live_weight)

    account = None
    if not is_admin:
        account = _owner_goat_account_snapshot(request.user)

    goat_shed = _goat_sheds().first()

    context = {
        "is_admin": is_admin,
        "goat_rows": rows,
        "goats": goats,
        "active_goat_count": len(active_rows),
        "male_count": len(active_males),
        "female_count": len(active_females),
        "total_goat_count": len(rows),
        "total_purchase_cost": total_purchase_cost,
        "total_operating_cost": total_operating_cost,
        "total_goat_cost": total_goat_cost,
        "total_live_weight": total_live_weight,
        "aggregate_cost_per_kg": aggregate_cost_per_kg,
        "owner_count": owner_count,
        "page_title": "Goat Farm" if is_admin else "My Goats",
        "account": account,
        "goat_sensor": _goat_sensor_snapshot(goat_shed),
        "goat_shed": goat_shed,
    }

    return render(request, "api/goat_dashboard.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def add_goat(request):
    if not _is_admin(request.user):
        messages.error(request, "Only Admin can add goats.")
        return redirect("goat_dashboard")

    owners = _goat_owner_choices()
    sheds = _goat_sheds()

    if request.method == "POST":
        owner = get_object_or_404(owners, pk=request.POST.get("owner_id"))
        shed = get_object_or_404(sheds, pk=request.POST.get("shed_id"))

        name = (request.POST.get("name") or "").strip()
        breed = (request.POST.get("breed") or "").strip()
        sex = request.POST.get("sex")
        acquisition_type = request.POST.get("acquisition_type") or "purchased"
        purchase_date = request.POST.get("purchase_date") or timezone.localdate()
        date_of_birth = request.POST.get("date_of_birth") or None
        notes = (request.POST.get("notes") or "").strip()

        try:
            purchase_cost = _money(request.POST.get("purchase_cost") or "0.00")
            weight_raw = request.POST.get("purchase_weight_kg")
            purchase_weight = _money(weight_raw) if weight_raw else None
        except (InvalidOperation, TypeError, ValueError):
            messages.error(request, "Enter valid purchase cost and weight values.")
            return render(request, "api/add_goat.html", {
                "owners": owners,
                "sheds": sheds,
                "today": timezone.localdate(),
            })

        if sex not in {"male", "female"}:
            messages.error(request, "Please select Male or Female.")
            return redirect("add_goat")

        if acquisition_type not in {"purchased", "born_on_farm"}:
            acquisition_type = "purchased"

        if purchase_cost < ZERO:
            messages.error(request, "Purchase cost cannot be negative.")
            return redirect("add_goat")

        if purchase_weight is not None and purchase_weight <= ZERO:
            messages.error(request, "Weight must be greater than zero.")
            return redirect("add_goat")

        goat = Goat.objects.create(
            name=name,
            breed=breed,
            sex=sex,
            owner=owner,
            shed=shed,
            shed_label=shed.name,
            acquisition_type=acquisition_type,
            purchase_date=purchase_date,
            date_of_birth=date_of_birth,
            purchase_cost=purchase_cost,
            purchase_weight_kg=purchase_weight,
            status="active",
            notes=notes,
            created_by=request.user,
        )

        if purchase_weight is not None:
            GoatWeightRecord.objects.create(
                goat=goat,
                record_date=purchase_date,
                weight_kg=purchase_weight,
                notes="Initial / purchase weight",
                recorded_by=request.user,
            )

        messages.success(request, f"{goat.goat_code} added successfully.")
        return redirect("goat_detail", goat_id=goat.pk)

    return render(request, "api/add_goat.html", {
        "owners": owners,
        "sheds": sheds,
        "today": timezone.localdate(),
    })


@login_required
def goat_detail(request, goat_id):
    goat = get_object_or_404(
        Goat.objects.select_related("owner", "shed"),
        pk=goat_id,
    )

    if not _is_admin(request.user) and goat.owner_id != request.user.id:
        messages.error(request, "You can only view goats that belong to your account.")
        return redirect("goat_dashboard")

    weight_records = goat.weight_records.select_related("recorded_by").all()
    finance = goat_finance_snapshot(goat)
    cost_allocations = (
        goat.cost_allocations
        .select_related("cost_entry", "owner_snapshot")
        .order_by("-cost_entry__entry_date", "-id")
    )

    purchase_weight = goat.purchase_weight_kg or ZERO
    current_weight = goat.current_weight_kg or ZERO
    weight_gain = current_weight - purchase_weight

    return render(request, "api/goat_detail.html", {
        "goat": goat,
        "weight_records": weight_records,
        "current_weight": current_weight,
        "weight_gain": weight_gain,
        "finance": finance,
        "cost_allocations": cost_allocations,
        "is_admin": _is_admin(request.user),
    })


@login_required
@require_http_methods(["GET", "POST"])
def add_goat_weight(request, goat_id):
    if not _is_admin(request.user):
        messages.error(request, "Only Admin can record goat weights.")
        return redirect("goat_dashboard")

    goat = get_object_or_404(Goat, pk=goat_id)

    if request.method == "POST":
        record_date = request.POST.get("record_date") or timezone.localdate()
        notes = (request.POST.get("notes") or "").strip()

        try:
            weight = _money(request.POST.get("weight_kg") or "0")
        except (InvalidOperation, TypeError, ValueError):
            messages.error(request, "Enter a valid weight.")
            return redirect("add_goat_weight", goat_id=goat.id)

        if weight <= ZERO:
            messages.error(request, "Weight must be greater than zero.")
            return redirect("add_goat_weight", goat_id=goat.id)

        GoatWeightRecord.objects.create(
            goat=goat,
            record_date=record_date,
            weight_kg=weight,
            notes=notes,
            recorded_by=request.user,
        )

        messages.success(request, f"Weight recorded for {goat.goat_code}.")
        return redirect("goat_detail", goat_id=goat.pk)

    return render(request, "api/add_goat_weight.html", {
        "goat": goat,
        "today": timezone.localdate(),
    })


@login_required
def goat_costs(request):
    if not _can_view_goats(request.user):
        messages.error(request, "You do not have permission to view Goat Costs.")
        return redirect("dashboard")

    is_admin = _is_admin(request.user)
    entries = (
        GoatCostEntry.objects
        .select_related("shed", "created_by")
        .prefetch_related("allocations__goat", "allocations__owner_snapshot")
        .order_by("-entry_date", "-id")
    )

    if not is_admin:
        entries = entries.filter(allocations__owner_snapshot=request.user).distinct()

    category_filter = request.GET.get("category", "all")
    if category_filter in {"feed", "medicine", "expense"}:
        entries = entries.filter(category=category_filter)

    entry_rows = []
    for entry in entries:
        allocations = list(entry.allocations.all())
        if not is_admin:
            allocations = [a for a in allocations if a.owner_snapshot_id == request.user.id]

        display_amount = _money(sum((a.amount for a in allocations), ZERO))
        entry_rows.append({
            "entry": entry,
            "allocations": allocations,
            "display_amount": display_amount,
            "goat_count": len(allocations),
        })

    total_display = _money(sum((row["display_amount"] for row in entry_rows), ZERO))

    return render(request, "api/goat_costs.html", {
        "entry_rows": entry_rows,
        "total_display": total_display,
        "category_filter": category_filter,
        "is_admin": is_admin,
    })


@login_required
@require_http_methods(["GET", "POST"])
def add_goat_cost(request, category):
    if not _is_admin(request.user):
        messages.error(request, "Only Admin can record Goat costs.")
        return redirect("goat_costs")

    if category not in {"feed", "medicine", "expense"}:
        messages.error(request, "Unknown Goat cost type.")
        return redirect("goat_costs")

    active_goats = list(
        Goat.objects
        .filter(status="active")
        .select_related("owner", "shed")
        .order_by("shed__name", "goat_code")
    )
    sheds = list(_goat_sheds())

    if request.method == "POST":
        try:
            total_amount = _money(request.POST.get("amount") or "0")
        except (InvalidOperation, TypeError, ValueError):
            messages.error(request, "Enter a valid amount.")
            return redirect("add_goat_cost", category=category)

        if total_amount <= ZERO:
            messages.error(request, "Amount must be greater than zero.")
            return redirect("add_goat_cost", category=category)

        shed = get_object_or_404(_goat_sheds(), pk=request.POST.get("shed_id"))
        scope = request.POST.get("allocation_scope") or "all_active"
        if scope not in {"individual", "selected", "all_active"}:
            scope = "all_active"

        shed_goats = [g for g in active_goats if g.shed_id == shed.id]

        if scope == "all_active":
            targets = shed_goats
        else:
            ids = request.POST.getlist("goat_ids")
            if scope == "individual":
                single_id = request.POST.get("goat_id")
                ids = [single_id] if single_id else []
            valid_ids = {str(g.id) for g in shed_goats}
            target_ids = {str(item) for item in ids if str(item) in valid_ids}
            targets = [g for g in shed_goats if str(g.id) in target_ids]

        if not targets:
            messages.error(request, "Select at least one active goat for this cost.")
            return redirect("add_goat_cost", category=category)

        title = (request.POST.get("title") or "").strip()
        medicine_type = (request.POST.get("medicine_type") or "").strip()
        expense_category = (request.POST.get("expense_category") or "").strip()
        notes = (request.POST.get("notes") or "").strip()
        entry_date = request.POST.get("entry_date") or timezone.localdate()

        if category == "medicine":
            valid_types = {key for key, _ in GoatCostEntry.MEDICINE_TYPE_CHOICES}
            if medicine_type not in valid_types:
                medicine_type = "medicine"
        else:
            medicine_type = ""

        if category == "expense":
            valid_expenses = {key for key, _ in GoatCostEntry.EXPENSE_CATEGORY_CHOICES}
            if expense_category not in valid_expenses:
                expense_category = "misc"
        else:
            expense_category = ""

        with transaction.atomic():
            entry = GoatCostEntry.objects.create(
                category=category,
                shed=shed,
                entry_date=entry_date,
                amount=total_amount,
                title=title,
                medicine_type=medicine_type,
                expense_category=expense_category,
                allocation_scope=scope,
                notes=notes,
                created_by=request.user,
            )

            for goat, allocated_amount in _allocate_exact_amount(total_amount, targets):
                GoatCostAllocation.objects.create(
                    cost_entry=entry,
                    goat=goat,
                    owner_snapshot=goat.owner,
                    amount=allocated_amount,
                )

        messages.success(
            request,
            f"{entry.get_category_display()} cost of Rs {total_amount:,.2f} allocated to {len(targets)} goat(s).",
        )
        return redirect("goat_costs")

    return render(request, "api/add_goat_cost.html", {
        "category": category,
        "category_label": dict(GoatCostEntry.CATEGORY_CHOICES)[category],
        "active_goats": active_goats,
        "sheds": sheds,
        "medicine_types": GoatCostEntry.MEDICINE_TYPE_CHOICES,
        "expense_categories": GoatCostEntry.EXPENSE_CATEGORY_CHOICES,
        "today": timezone.localdate(),
    })


@login_required
def goat_finance(request):
    if not _can_view_goats(request.user):
        messages.error(request, "You do not have permission to view Goat Finance.")
        return redirect("dashboard")

    is_admin = _is_admin(request.user)
    goats = list(_visible_goats(request.user).order_by("status", "goat_code", "id"))
    rows = [goat_finance_snapshot(goat) for goat in goats]

    total_purchase = _money(sum((row["purchase_cost"] for row in rows), ZERO))
    total_feed = _money(sum((row["feed_cost"] for row in rows), ZERO))
    total_medicine = _money(sum((row["medicine_cost"] for row in rows), ZERO))
    total_expense = _money(sum((row["expense_cost"] for row in rows), ZERO))
    total_cost = _money(sum((row["total_cost"] for row in rows), ZERO))
    total_sales = _money(sum((row["sale_revenue"] for row in rows), ZERO))
    realized_profit = _money(sum((row["sale_profit"] for row in rows if row["sale"]), ZERO))

    sold_cost = _money(sum((row["sale"].locked_total_cost for row in rows if row["sale"]), ZERO))
    realized_roi = ZERO
    if sold_cost > ZERO:
        realized_roi = _money(realized_profit / sold_cost * Decimal("100"))

    active_rows = [row for row in rows if row["goat"].status == "active"]
    active_cost = _money(sum((row["total_cost"] for row in active_rows), ZERO))
    active_weight = _money(sum((row["current_weight"] for row in active_rows), ZERO))
    active_break_even = ZERO
    if active_weight > ZERO:
        active_break_even = _money(active_cost / active_weight)

    account = None
    if not is_admin:
        account = _owner_goat_account_snapshot(request.user)

    return render(request, "api/goat_finance.html", {
        "rows": rows,
        "is_admin": is_admin,
        "total_purchase": total_purchase,
        "total_feed": total_feed,
        "total_medicine": total_medicine,
        "total_expense": total_expense,
        "total_cost": total_cost,
        "total_sales": total_sales,
        "realized_profit": realized_profit,
        "realized_roi": realized_roi,
        "active_break_even": active_break_even,
        "account": account,
    })


@login_required
@require_http_methods(["GET", "POST"])
def add_goat_sale(request, goat_id):
    if not _is_admin(request.user):
        messages.error(request, "Only Admin can record Goat sales.")
        return redirect("goat_finance")

    goat = get_object_or_404(
        Goat.objects.select_related("owner", "shed"),
        pk=goat_id,
    )

    if goat.status != "active":
        messages.error(request, "Only an active goat can be sold.")
        return redirect("goat_detail", goat_id=goat.id)

    if GoatSale.objects.filter(goat=goat).exists():
        messages.error(request, "This goat already has a sale record.")
        return redirect("goat_detail", goat_id=goat.id)

    finance = goat_finance_snapshot(goat)

    if request.method == "POST":
        try:
            sale_weight = _money(request.POST.get("sale_weight_kg") or "0")
            rate = _money(request.POST.get("rate_per_kg") or "0")
            discount = _money(request.POST.get("discount_amount") or "0")
        except (InvalidOperation, TypeError, ValueError):
            messages.error(request, "Enter valid sale weight, rate and discount values.")
            return redirect("add_goat_sale", goat_id=goat.id)

        if sale_weight <= ZERO or rate <= ZERO:
            messages.error(request, "Sale weight and rate must be greater than zero.")
            return redirect("add_goat_sale", goat_id=goat.id)

        gross = _money(sale_weight * rate)
        if discount < ZERO or discount > gross:
            messages.error(request, "Discount cannot be negative or greater than the gross sale amount.")
            return redirect("add_goat_sale", goat_id=goat.id)

        total = _money(gross - discount)
        payment_method = request.POST.get("payment_method") or "cash"
        valid_methods = {key for key, _ in GoatSale.PAYMENT_METHOD_CHOICES}
        if payment_method not in valid_methods:
            payment_method = "other"

        sale_date = request.POST.get("sale_date") or timezone.localdate()

        with transaction.atomic():
            sale = GoatSale.objects.create(
                goat=goat,
                sale_date=sale_date,
                buyer_name=(request.POST.get("buyer_name") or "").strip(),
                sale_weight_kg=sale_weight,
                rate_per_kg=rate,
                gross_amount=gross,
                discount_amount=discount,
                total_amount=total,
                locked_total_cost=finance["total_cost"],
                payment_method=payment_method,
                notes=(request.POST.get("notes") or "").strip(),
                recorded_by=request.user,
            )

            GoatWeightRecord.objects.create(
                goat=goat,
                record_date=sale_date,
                weight_kg=sale_weight,
                notes="Sale weight",
                recorded_by=request.user,
            )

            goat.status = "sold"
            goat.save(update_fields=["status", "updated_at"])

        messages.success(
            request,
            f"Sale recorded for {goat.goat_code}. Profit/Loss: Rs {sale.profit:,.2f}.",
        )
        return redirect("goat_detail", goat_id=goat.id)

    return render(request, "api/add_goat_sale.html", {
        "goat": goat,
        "finance": finance,
        "payment_methods": GoatSale.PAYMENT_METHOD_CHOICES,
        "today": timezone.localdate(),
    })


@login_required
def goat_accounts(request):
    is_admin = _is_admin(request.user)
    investor_profile = getattr(request.user, "investor_profile", None)

    if not is_admin and investor_profile is None:
        messages.error(request, "You do not have permission to view Goat accounts.")
        return redirect("dashboard")

    if is_admin:
        owners = (
            User.objects
            .filter(investor_profile__isnull=False, owned_goats__isnull=False)
            .distinct()
            .order_by("username")
        )
    else:
        owners = User.objects.filter(pk=request.user.pk)

    accounts = [_owner_goat_account_snapshot(owner) for owner in owners]

    total_cost = _money(sum((row["total_cost"] for row in accounts), ZERO))
    total_paid = _money(sum((row["total_paid"] for row in accounts), ZERO))
    total_outstanding = _money(sum((row["outstanding"] for row in accounts), ZERO))
    total_credit = _money(sum((row["credit"] for row in accounts), ZERO))

    return render(request, "api/goat_accounts.html", {
        "accounts": accounts,
        "is_admin": is_admin,
        "total_cost": total_cost,
        "total_paid": total_paid,
        "total_outstanding": total_outstanding,
        "total_credit": total_credit,
    })


@login_required
def goat_account_detail(request, owner_id):
    owner = get_object_or_404(User, pk=owner_id)

    if not _is_admin(request.user) and owner.id != request.user.id:
        messages.error(request, "You can only view your own Goat account.")
        return redirect("goat_accounts")

    if not Goat.objects.filter(owner=owner).exists():
        messages.error(request, "No Goat account exists for this owner.")
        return redirect("goat_accounts")

    account = _owner_goat_account_snapshot(owner)
    statement_rows = _build_owner_statement(owner)

    return render(request, "api/goat_account_detail.html", {
        "account": account,
        "statement_rows": statement_rows,
        "is_admin": _is_admin(request.user),
        "payment_methods": GoatAccountPayment.PAYMENT_METHOD_CHOICES,
        "today": timezone.localdate(),
    })


@login_required
@require_POST
def record_goat_account_payment(request, owner_id):
    if not _is_admin(request.user):
        messages.error(request, "Only Admin can record Goat investor payments.")
        return redirect("goat_accounts")

    owner = get_object_or_404(
        User.objects.filter(investor_profile__isnull=False),
        pk=owner_id,
    )

    try:
        amount = _money(request.POST.get("amount") or "0")
    except (InvalidOperation, TypeError, ValueError):
        messages.error(request, "Enter a valid payment amount.")
        return redirect("goat_account_detail", owner_id=owner.id)

    if amount <= ZERO:
        messages.error(request, "Payment amount must be greater than zero.")
        return redirect("goat_account_detail", owner_id=owner.id)

    payment_method = request.POST.get("payment_method") or "bank_transfer"
    valid_methods = {key for key, _ in GoatAccountPayment.PAYMENT_METHOD_CHOICES}
    if payment_method not in valid_methods:
        payment_method = "other"

    GoatAccountPayment.objects.create(
        owner=owner,
        payment_date=request.POST.get("payment_date") or timezone.localdate(),
        amount=amount,
        payment_method=payment_method,
        reference=(request.POST.get("reference") or "").strip(),
        notes=(request.POST.get("notes") or "").strip(),
        recorded_by=request.user,
    )

    owner_name = owner.get_full_name().strip() or owner.username
    messages.success(request, f"Goat account payment of Rs {amount:,.2f} recorded for {owner_name}.")
    return redirect("goat_account_detail", owner_id=owner.id)
