from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from api.models.investors import (
    FeedEntry,
    InvestorAccountPayment,
    InvestorAllocation,
    MedicineEntry,
)
from api.models.sales import ChickCostEntry, Expense


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
    return user.is_superuser or user.is_staff


def _investor_name(allocation):
    user = allocation.investor.user
    return user.get_full_name().strip() or user.username


def _share_ratio(allocation):
    starting_birds = int(allocation.batch.bird_count_initial or 0)
    if starting_birds <= 0:
        return ZERO
    return (
        Decimal(allocation.birds_owned)
        / Decimal(starting_birds)
    )


def _batch_cost_totals(batch):
    chick_cost = (
        ChickCostEntry.objects.filter(batch=batch)
        .aggregate(total=Sum("chick_cost"))["total"]
        or ZERO
    )
    carriage_cost = (
        ChickCostEntry.objects.filter(batch=batch)
        .aggregate(total=Sum("carriage_cost"))["total"]
        or ZERO
    )
    feed_cost = (
        FeedEntry.objects.filter(batch=batch)
        .aggregate(total=Sum("amount"))["total"]
        or ZERO
    )
    medicine_cost = (
        MedicineEntry.objects.filter(batch=batch)
        .aggregate(total=Sum("amount"))["total"]
        or ZERO
    )
    expense_cost = (
        Expense.objects.filter(batch=batch)
        .aggregate(total=Sum("amount"))["total"]
        or ZERO
    )

    totals = {
        "chick_cost": _money(chick_cost),
        "carriage_cost": _money(carriage_cost),
        "feed_cost": _money(feed_cost),
        "medicine_cost": _money(medicine_cost),
        "expense_cost": _money(expense_cost),
    }
    totals["total_cost"] = _money(sum(totals.values(), ZERO))
    return totals


def _account_snapshot(allocation):
    ratio = _share_ratio(allocation)
    batch_costs = _batch_cost_totals(allocation.batch)

    shares = {
        key: _money(value * ratio)
        for key, value in batch_costs.items()
        if key != "total_cost"
    }
    shares["total_cost"] = _money(sum(shares.values(), ZERO))

    total_paid = (
        allocation.account_payments.aggregate(total=Sum("amount"))["total"]
        or ZERO
    )
    total_paid = _money(total_paid)

    raw_balance = _money(shares["total_cost"] - total_paid)

    # The UI displays financial figures rounded to whole rupees.
    # Ignore sub-50-paisa differences for account status so a screen
    # showing Cost Rs 57,934 / Paid Rs 57,934 / Credit Rs 0 does not
    # incorrectly say "Credit" or "Outstanding". Exact payment
    # records and cost calculations are still preserved to the paisa.
    if abs(raw_balance) < ACCOUNT_TOLERANCE:
        raw_balance = ZERO

    outstanding = max(raw_balance, ZERO)
    credit = max(-raw_balance, ZERO)

    if credit > ZERO:
        status = "credit"
        status_label = "Credit"
    elif outstanding <= ZERO and shares["total_cost"] > ZERO:
        status = "paid"
        status_label = "Paid"
    elif total_paid > ZERO:
        status = "partial"
        status_label = "Partial"
    elif shares["total_cost"] > ZERO:
        status = "unpaid"
        status_label = "Unpaid"
    else:
        status = "no_cost"
        status_label = "No Cost"

    return {
        "allocation": allocation,
        "investor_name": _investor_name(allocation),
        "batch": allocation.batch,
        "share_ratio": ratio,
        "share_percentage": (ratio * Decimal("100")).quantize(
            Decimal("0.1"),
            rounding=ROUND_HALF_UP,
        ),
        "birds_owned": allocation.birds_owned,
        "chick_cost_share": shares["chick_cost"],
        "carriage_cost_share": shares["carriage_cost"],
        "feed_cost_share": shares["feed_cost"],
        "medicine_cost_share": shares["medicine_cost"],
        "expense_cost_share": shares["expense_cost"],
        "total_cost_share": shares["total_cost"],
        "total_paid": total_paid,
        "outstanding": _money(outstanding),
        "credit": _money(credit),
        "status": status,
        "status_label": status_label,
    }


def _authorized_allocation(request, allocation_id):
    allocation = get_object_or_404(
        InvestorAllocation.objects.select_related(
            "batch__shed",
            "investor__user",
        ),
        id=allocation_id,
    )

    if _is_admin(request.user):
        return allocation

    investor_profile = getattr(request.user, "investor_profile", None)
    if investor_profile and allocation.investor_id == investor_profile.id:
        return allocation

    return None


def _build_statement(allocation):
    ratio = _share_ratio(allocation)
    rows = []

    chick_entries = ChickCostEntry.objects.filter(
        batch=allocation.batch
    ).order_by("entry_date", "id")

    for entry in chick_entries:
        if entry.chick_cost and entry.chick_cost > ZERO:
            rows.append({
                "date": entry.entry_date,
                "sort_type": 0,
                "sort_id": entry.id * 10,
                "entry_type": "charge",
                "category": "Chick Cost",
                "description": entry.notes or "Chick purchase",
                "charge": _money(entry.chick_cost * ratio),
                "payment": ZERO,
            })

        if entry.carriage_cost and entry.carriage_cost > ZERO:
            rows.append({
                "date": entry.entry_date,
                "sort_type": 0,
                "sort_id": entry.id * 10 + 1,
                "entry_type": "charge",
                "category": "Carriage",
                "description": entry.notes or "Chick carriage / delivery",
                "charge": _money(entry.carriage_cost * ratio),
                "payment": ZERO,
            })

    for entry in FeedEntry.objects.filter(
        batch=allocation.batch
    ).order_by("entry_date", "id"):
        rows.append({
            "date": entry.entry_date,
            "sort_type": 0,
            "sort_id": 100000 + entry.id,
            "entry_type": "charge",
            "category": "Feed",
            "description": entry.notes or "Feed purchase",
            "charge": _money(entry.amount * ratio),
            "payment": ZERO,
        })

    for entry in MedicineEntry.objects.filter(
        batch=allocation.batch
    ).order_by("entry_date", "id"):
        description = (
            f"{entry.get_medicine_type_display()} · "
            f"{entry.medicine_name}"
        )
        if entry.notes:
            description += f" — {entry.notes}"

        rows.append({
            "date": entry.entry_date,
            "sort_type": 0,
            "sort_id": 200000 + entry.id,
            "entry_type": "charge",
            "category": "Medicine",
            "description": description,
            "charge": _money(entry.amount * ratio),
            "payment": ZERO,
        })

    for entry in Expense.objects.filter(
        batch=allocation.batch
    ).order_by("expense_date", "id"):
        description = entry.get_category_display()
        if entry.description:
            description += f" — {entry.description}"

        rows.append({
            "date": entry.expense_date,
            "sort_type": 0,
            "sort_id": 300000 + entry.id,
            "entry_type": "charge",
            "category": "Expense",
            "description": description,
            "charge": _money(entry.amount * ratio),
            "payment": ZERO,
        })

    for payment in allocation.account_payments.all().order_by(
        "payment_date",
        "id",
    ):
        description = payment.get_payment_method_display()
        if payment.reference:
            description += f" · {payment.reference}"
        if payment.notes:
            description += f" — {payment.notes}"

        rows.append({
            "date": payment.payment_date,
            "sort_type": 1,
            "sort_id": 400000 + payment.id,
            "entry_type": "payment",
            "category": "Payment Received",
            "description": description,
            "charge": ZERO,
            "payment": _money(payment.amount),
            "payment_record": payment,
        })

    rows.sort(
        key=lambda row: (
            row["date"],
            row["sort_type"],
            row["sort_id"],
        )
    )

    running_balance = ZERO
    for row in rows:
        running_balance = _money(
            running_balance
            + row["charge"]
            - row["payment"]
        )
        row["running_balance"] = running_balance

    return rows


@login_required
def investor_accounts(request):
    is_admin = _is_admin(request.user)
    investor_profile = getattr(request.user, "investor_profile", None)

    if not is_admin and investor_profile is None:
        messages.error(
            request,
            "You do not have permission to view investor accounts.",
        )
        return redirect("dashboard")

    allocations = InvestorAllocation.objects.select_related(
        "batch__shed",
        "investor__user",
    )

    if not is_admin:
        allocations = allocations.filter(investor=investor_profile)

    allocations = allocations.order_by(
        "-batch__start_date",
        "batch__batch_number",
        "investor__user__username",
    )

    accounts = [_account_snapshot(item) for item in allocations]

    total_cost_share = _money(sum(
        (item["total_cost_share"] for item in accounts),
        ZERO,
    ))
    total_paid = _money(sum(
        (item["total_paid"] for item in accounts),
        ZERO,
    ))
    total_outstanding = _money(sum(
        (item["outstanding"] for item in accounts),
        ZERO,
    ))
    total_credit = _money(sum(
        (item["credit"] for item in accounts),
        ZERO,
    ))
    outstanding_accounts = sum(
        1 for item in accounts if item["outstanding"] > ZERO
    )

    return render(
        request,
        "api/investor_accounts.html",
        {
            "accounts": accounts,
            "is_admin": is_admin,
            "total_cost_share": total_cost_share,
            "total_paid": total_paid,
            "total_outstanding": total_outstanding,
            "total_credit": total_credit,
            "outstanding_accounts": outstanding_accounts,
        },
    )


@login_required
def investor_account_detail(request, allocation_id):
    allocation = _authorized_allocation(request, allocation_id)
    if allocation is None:
        messages.error(
            request,
            "You can only view your own investor account.",
        )
        return redirect("investor_accounts")

    snapshot = _account_snapshot(allocation)
    statement_rows = _build_statement(allocation)

    return render(
        request,
        "api/investor_account_detail.html",
        {
            "account": snapshot,
            "statement_rows": statement_rows,
            "is_admin": _is_admin(request.user),
            "payment_methods": InvestorAccountPayment.PAYMENT_METHOD_CHOICES,
            "today": timezone.localdate().isoformat(),
        },
    )


@login_required
@require_POST
def record_investor_account_payment(request, allocation_id):
    if not _is_admin(request.user):
        messages.error(
            request,
            "Only Admin can record investor payments.",
        )
        return redirect("investor_accounts")

    allocation = get_object_or_404(
        InvestorAllocation.objects.select_related(
            "batch",
            "investor__user",
        ),
        id=allocation_id,
    )

    try:
        amount = Decimal(
            str(request.POST.get("amount", "0") or "0")
        ).quantize(MONEY)
    except (InvalidOperation, TypeError, ValueError):
        messages.error(request, "Enter a valid payment amount.")
        return redirect(
            "investor_account_detail",
            allocation_id=allocation.id,
        )

    if amount <= ZERO:
        messages.error(
            request,
            "Payment amount must be greater than zero.",
        )
        return redirect(
            "investor_account_detail",
            allocation_id=allocation.id,
        )

    payment_date = request.POST.get("payment_date") or timezone.localdate()
    payment_method = request.POST.get("payment_method") or "bank_transfer"
    valid_methods = {
        key for key, _label in InvestorAccountPayment.PAYMENT_METHOD_CHOICES
    }
    if payment_method not in valid_methods:
        payment_method = "other"

    InvestorAccountPayment.objects.create(
        allocation=allocation,
        payment_date=payment_date,
        amount=amount,
        payment_method=payment_method,
        reference=(request.POST.get("reference") or "").strip(),
        notes=(request.POST.get("notes") or "").strip(),
        recorded_by=request.user,
    )

    investor_name = _investor_name(allocation)
    messages.success(
        request,
        f"Payment of Rs {amount:,.2f} recorded for {investor_name}.",
    )

    return redirect(
        "investor_account_detail",
        allocation_id=allocation.id,
    )
