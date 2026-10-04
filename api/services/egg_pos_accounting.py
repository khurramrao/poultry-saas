from decimal import Decimal

from django.db.models import Sum

from api.models.egg_pos import (
    EggPOSCashSettlement,
    EggPOSCommissionPayment,
    EggPOSCommissionPeriod,
    EggPOSOwnerCapitalTransaction,
    EggPOSExpense,
    EggPOSFarmTransfer,
    EggPOSFarmTransferPayment,
    EggPOSPurchase,
    EggPOSSale,
    EggPOSSalePayment,
    EggPOSSupplierPayment,
)
from api.services.accounting import line, money, post_entry


ZERO = Decimal("0.00")


def _user_name(user):
    if not user:
        return ""
    return user.get_full_name().strip() or user.username


def _customer_party(sale):
    if sale.customer_id:
        return {
            "party_type": "customer",
            "party_id": sale.customer_id,
            "party_name": sale.customer.display_label if hasattr(sale, "customer") and sale.customer else (sale.customer_name or "Customer"),
        }
    return {
        "party_type": "customer",
        "party_id": None,
        "party_name": sale.customer_name or "Walk-in Customer",
    }


def _supplier_party(supplier):
    return {"party_type": "supplier", "party_id": supplier.id, "party_name": supplier.name}


def _user_party(user):
    return {"party_type": "user", "party_id": user.id, "party_name": _user_name(user)}


def _farm_party(batch=None):
    return {
        "party_type": "farm",
        "party_id": getattr(batch, "id", None),
        "party_name": "RayNoor Egg Production",
    }


def _company_payment_account(method):
    if method in {"bank_transfer", "easypaisa", "jazzcash"}:
        return "1010"
    return "1000"


def _incoming_payment_debit(payment):
    """Return (account_code, optional party kwargs) for money received from a customer."""
    if payment.payment_method in {"bank_transfer", "easypaisa", "jazzcash"}:
        return "1010", {}
    if payment.payment_method == "cash":
        user = payment.recorded_by
        access = getattr(user, "egg_pos_access", None)
        is_salesperson = bool(
            not user.is_superuser
            and not user.is_staff
            and access
            and access.is_active
            and access.can_sell
            and not access.can_manage
        )
        if is_salesperson:
            return "1040", _user_party(user)
    return "1000", {}


def sync_purchase(purchase):
    base_total = money(purchase.total_amount)
    transport = money(purchase.transport_cost)
    landed = money(base_total + transport)
    supplier_party = _supplier_party(purchase.supplier)
    lines = [
        line("1200", debit=landed, description="Egg inventory received"),
        line("2000", credit=base_total, description="Supplier invoice", **supplier_party),
    ]
    if transport > ZERO:
        if purchase.transport_payment_method == "supplier_payable":
            lines.append(line("2000", credit=transport, description="Inbound transport added to supplier payable", **supplier_party))
        else:
            lines.append(
                line(
                    _company_payment_account(purchase.transport_payment_method),
                    credit=transport,
                    description="Inbound transport capitalized into inventory",
                )
            )
    return post_entry(
        source_key=f"egg_pos:purchase:{purchase.id}",
        entry_date=purchase.purchase_date,
        reference=f"PUR-{purchase.id:05d}",
        memo=f"Egg purchase from {purchase.supplier.name}",
        source_type="egg_pos_purchase",
        source_id=purchase.id,
        created_by=purchase.created_by,
        lines=lines,
    )


def sync_supplier_payment(payment):
    party = _supplier_party(payment.supplier)
    return post_entry(
        source_key=f"egg_pos:supplier_payment:{payment.id}",
        entry_date=payment.payment_date,
        reference=payment.reference or f"SUPPAY-{payment.id:05d}",
        memo=f"Payment to supplier {payment.supplier.name}",
        source_type="egg_pos_supplier_payment",
        source_id=payment.id,
        created_by=payment.recorded_by,
        lines=[
            line("2000", debit=payment.amount, description="Supplier payment", **party),
            line(_company_payment_account(payment.payment_method), credit=payment.amount, description="Payment made"),
        ],
    )


def sync_farm_transfer(transfer):
    base_total = money(transfer.total_amount)
    transport = money(transfer.transport_cost)
    landed = money(base_total + transport)
    farm_party = _farm_party(transfer.batch)
    lines = [
        line("1200", debit=landed, description="Farm eggs received into POS inventory"),
        line("2010", credit=base_total, description="Internal payable to RayNoor Egg Production", **farm_party),
    ]
    if transport > ZERO:
        lines.append(
            line(
                _company_payment_account(transfer.transport_payment_method),
                credit=transport,
                description="Inbound transport capitalized into inventory",
            )
        )
    return post_entry(
        source_key=f"egg_pos:farm_transfer:{transfer.id}",
        entry_date=transfer.transfer_date,
        reference=transfer.transfer_number or f"RNET-{transfer.id:05d}",
        memo="Farm to Egg POS internal inventory transfer",
        source_type="egg_pos_farm_transfer",
        source_id=transfer.id,
        created_by=transfer.created_by,
        lines=lines,
    )


def sync_farm_transfer_payment(payment):
    party = _farm_party(payment.transfer.batch)
    return post_entry(
        source_key=f"egg_pos:farm_transfer_payment:{payment.id}",
        entry_date=payment.payment_date,
        reference=payment.reference or f"RN-PAY-{payment.id:05d}",
        memo=f"Payment against {payment.transfer.transfer_number or payment.transfer_id}",
        source_type="egg_pos_farm_transfer_payment",
        source_id=payment.id,
        created_by=payment.recorded_by,
        lines=[
            line("2010", debit=payment.amount, description="Farm payable settled", **party),
            line(_company_payment_account(payment.payment_method), credit=payment.amount, description="Payment made"),
        ],
    )


def sync_sale_invoice(sale):
    customer_party = _customer_party(sale)
    return post_entry(
        source_key=f"egg_pos:sale:{sale.id}",
        entry_date=sale.sale_date,
        reference=sale.sale_number or f"RNE-{sale.id:05d}",
        memo=f"Egg sale by {_user_name(sale.created_by)}",
        source_type="egg_pos_sale",
        source_id=sale.id,
        created_by=sale.created_by,
        lines=[
            line("1100", debit=sale.net_total, description="Customer invoice", **customer_party),
            line("4000", credit=sale.net_total, description="Egg sales revenue"),
            line("5000", debit=sale.cogs_total, description="FIFO egg cost of goods sold"),
            line("1200", credit=sale.cogs_total, description="FIFO inventory consumed"),
        ],
    )


def sync_sale_payment(payment):
    customer_party = _customer_party(payment.sale)
    debit_code, debit_party = _incoming_payment_debit(payment)
    return post_entry(
        source_key=f"egg_pos:sale_payment:{payment.id}",
        entry_date=payment.payment_date,
        reference=payment.reference or f"{payment.sale.sale_number}-PAY-{payment.id}",
        memo=f"Customer payment for {payment.sale.sale_number}",
        source_type="egg_pos_sale_payment",
        source_id=payment.id,
        created_by=payment.recorded_by,
        lines=[
            line(debit_code, debit=payment.amount, description="Customer payment received", **debit_party),
            line("1100", credit=payment.amount, description="Customer receivable settled", **customer_party),
        ],
    )


def _expense_account_code(expense_type):
    return {
        "fuel": "6100",
        "delivery": "6110",
        "toll_parking": "6120",
        "packaging": "6130",
        "other": "6140",
    }.get(expense_type, "6140")


def sync_expense(expense):
    source_key = f"egg_pos:expense:{expense.id}"
    if expense.status != "approved":
        # An unapproved/rejected expense must never hit the books.
        from api.models.accounting import JournalEntry
        JournalEntry.objects.filter(source_key=source_key).delete()
        return None

    user_party = _user_party(expense.used_by)
    credit_code = "1000"
    credit_party = {}
    if expense.payment_source == "sales_collection":
        credit_code = "1040"
        credit_party = user_party
    elif expense.payment_source == "personal":
        credit_code = "2020"
        credit_party = user_party
    elif expense.payment_source == "company_bank":
        credit_code = "1010"

    return post_entry(
        source_key=source_key,
        entry_date=expense.expense_date,
        reference=expense.reference or f"EXP-{expense.id:05d}",
        memo=f"{expense.get_expense_type_display()} used by {_user_name(expense.used_by)}",
        source_type="egg_pos_expense",
        source_id=expense.id,
        created_by=expense.approved_by or expense.entered_by,
        lines=[
            line(_expense_account_code(expense.expense_type), debit=expense.amount, description=expense.get_expense_type_display(), **user_party),
            line(credit_code, credit=expense.amount, description=expense.get_payment_source_display(), **credit_party),
        ],
    )


def sync_cash_settlement(settlement):
    user_party = _user_party(settlement.salesperson)
    debit_code = "1010" if settlement.destination == "bank_transfer" else "1000"
    return post_entry(
        source_key=f"egg_pos:cash_settlement:{settlement.id}",
        entry_date=settlement.settlement_date,
        reference=settlement.reference or f"SET-{settlement.id:05d}",
        memo=f"Cash handed over by {_user_name(settlement.salesperson)}",
        source_type="egg_pos_cash_settlement",
        source_id=settlement.id,
        created_by=settlement.recorded_by,
        lines=[
            line(debit_code, debit=settlement.amount, description="Cash received from salesperson"),
            line("1040", credit=settlement.amount, description="Salesperson cash cleared", **user_party),
        ],
    )


def commission_preview(salesperson, start_date, end_date):
    sales = EggPOSSale.objects.filter(
        created_by=salesperson,
        sale_date__gte=start_date,
        sale_date__lte=end_date,
    )
    sales_totals = sales.aggregate(revenue=Sum("net_total"), cogs=Sum("cogs_total"))
    revenue = money(sales_totals["revenue"] or ZERO)
    cogs = money(sales_totals["cogs"] or ZERO)
    expense_total = money(
        EggPOSExpense.objects.filter(
            used_by=salesperson,
            status="approved",
            affects_commission=True,
            expense_date__gte=start_date,
            expense_date__lte=end_date,
        ).aggregate(total=Sum("amount"))["total"]
        or ZERO
    )
    net_after_expenses = money(revenue - cogs - expense_total)
    commissionable_profit = money(max(net_after_expenses, ZERO))
    access = getattr(salesperson, "egg_pos_access", None)
    rate = Decimal(getattr(access, "commission_percent", ZERO) or ZERO)
    commission_amount = money(commissionable_profit * rate / Decimal("100"))
    return {
        "sales_revenue": revenue,
        "cogs": cogs,
        "gross_profit": money(revenue - cogs),
        "selling_expenses": expense_total,
        "net_after_expenses": net_after_expenses,
        "commissionable_profit": commissionable_profit,
        "commission_percent": rate,
        "commission_amount": commission_amount,
    }


def sync_commission_period(commission):
    if money(commission.commission_amount) <= ZERO:
        from api.models.accounting import JournalEntry
        JournalEntry.objects.filter(source_key=f"egg_pos:commission:{commission.id}").delete()
        return None
    party = _user_party(commission.salesperson)
    return post_entry(
        source_key=f"egg_pos:commission:{commission.id}",
        entry_date=commission.period_end,
        reference=f"COM-{commission.id:05d}",
        memo=f"Sales commission {_user_name(commission.salesperson)} {commission.period_start} to {commission.period_end}",
        source_type="egg_pos_commission",
        source_id=commission.id,
        created_by=commission.posted_by,
        lines=[
            line("6200", debit=commission.commission_amount, description="Sales commission expense", **party),
            line("2030", credit=commission.commission_amount, description="Commission payable", **party),
        ],
    )


def sync_commission_payment(payment):
    party = _user_party(payment.commission.salesperson)
    return post_entry(
        source_key=f"egg_pos:commission_payment:{payment.id}",
        entry_date=payment.payment_date,
        reference=payment.reference or f"COMPAY-{payment.id:05d}",
        memo=f"Commission payment to {_user_name(payment.commission.salesperson)}",
        source_type="egg_pos_commission_payment",
        source_id=payment.id,
        created_by=payment.recorded_by,
        lines=[
            line("2030", debit=payment.amount, description="Commission payable settled", **party),
            line(_company_payment_account(payment.payment_method), credit=payment.amount, description="Commission paid"),
        ],
    )



def sync_owner_capital(transaction):
    """Post owner funding without mixing permanent capital with temporary loans."""
    cash_code = "1010" if transaction.cash_account == "bank" else "1000"
    label = transaction.get_transaction_type_display()

    if transaction.transaction_type == "withdrawal":
        lines = [
            line("3000", debit=transaction.amount, description=label),
            line(cash_code, credit=transaction.amount, description="Owner withdrawal paid"),
        ]
        source_type = "egg_pos_owner_capital"
        reference = transaction.reference or f"CAP-{transaction.id:05d}"
    elif transaction.transaction_type == "owner_loan":
        lines = [
            line(cash_code, debit=transaction.amount, description="Temporary funds received from owner"),
            line("2040", credit=transaction.amount, description=label),
        ]
        source_type = "egg_pos_owner_loan"
        reference = transaction.reference or f"LOAN-{transaction.id:05d}"
    elif transaction.transaction_type == "loan_repayment":
        lines = [
            line("2040", debit=transaction.amount, description=label),
            line(cash_code, credit=transaction.amount, description="Owner loan repaid"),
        ]
        source_type = "egg_pos_owner_loan"
        reference = transaction.reference or f"LOANPAY-{transaction.id:05d}"
    else:
        lines = [
            line(cash_code, debit=transaction.amount, description="Owner funds introduced"),
            line("3000", credit=transaction.amount, description=label),
        ]
        source_type = "egg_pos_owner_capital"
        reference = transaction.reference or f"CAP-{transaction.id:05d}"

    return post_entry(
        source_key=f"egg_pos:owner_capital:{transaction.id}",
        entry_date=transaction.transaction_date,
        reference=reference,
        memo=label,
        source_type=source_type,
        source_id=transaction.id,
        created_by=transaction.recorded_by,
        lines=lines,
    )

def sync_all_egg_pos_books():
    """Idempotently rebuild journals from all recorded Egg POS transactions."""
    for capital in EggPOSOwnerCapitalTransaction.objects.select_related("recorded_by").order_by("id"):
        sync_owner_capital(capital)
    for purchase in EggPOSPurchase.objects.select_related("supplier", "created_by").prefetch_related("items").order_by("id"):
        sync_purchase(purchase)
    for payment in EggPOSSupplierPayment.objects.select_related("supplier", "purchase", "recorded_by").order_by("id"):
        sync_supplier_payment(payment)
    for transfer in EggPOSFarmTransfer.objects.select_related("batch", "created_by").prefetch_related("items").order_by("id"):
        sync_farm_transfer(transfer)
    for payment in EggPOSFarmTransferPayment.objects.select_related("transfer__batch", "recorded_by").order_by("id"):
        sync_farm_transfer_payment(payment)
    for sale in EggPOSSale.objects.select_related("customer", "created_by").order_by("id"):
        sync_sale_invoice(sale)
    for payment in EggPOSSalePayment.objects.select_related("sale__customer", "recorded_by").order_by("id"):
        sync_sale_payment(payment)
    for expense in EggPOSExpense.objects.select_related("used_by", "entered_by", "approved_by").order_by("id"):
        sync_expense(expense)
    for settlement in EggPOSCashSettlement.objects.select_related("salesperson", "recorded_by").order_by("id"):
        sync_cash_settlement(settlement)
    for commission in EggPOSCommissionPeriod.objects.select_related("salesperson", "posted_by").order_by("id"):
        sync_commission_period(commission)
    for payment in EggPOSCommissionPayment.objects.select_related("commission__salesperson", "recorded_by").order_by("id"):
        sync_commission_payment(payment)
