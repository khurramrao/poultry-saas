from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from api.models.accounting import ChartOfAccount, JournalEntry, JournalLine


ZERO = Decimal("0.00")
CENT = Decimal("0.01")


ACCOUNT_SPECS = {
    "1000": ("Main Cash", "asset", "debit"),
    "1010": ("Bank / Digital Wallet", "asset", "debit"),
    "1040": ("Cash Held by Sales Staff", "asset", "debit"),
    "1100": ("Accounts Receivable - Customers", "asset", "debit"),
    "1200": ("Egg Inventory", "asset", "debit"),
    "2000": ("Accounts Payable - Suppliers", "liability", "credit"),
    "2010": ("Due to RayNoor Egg Production", "liability", "credit"),
    "2020": ("Staff Reimbursements Payable", "liability", "credit"),
    "2030": ("Sales Commission Payable", "liability", "credit"),
    "2040": ("Owner Loan Payable", "liability", "credit"),
    "3000": ("Owner Capital / Equity", "equity", "credit"),
    "4000": ("Egg Sales", "revenue", "credit"),
    "5000": ("Egg Cost of Goods Sold", "cogs", "debit"),
    "6100": ("Fuel Expense", "expense", "debit"),
    "6110": ("Delivery / Rider Expense", "expense", "debit"),
    "6120": ("Toll / Parking Expense", "expense", "debit"),
    "6130": ("Packaging Expense", "expense", "debit"),
    "6140": ("Other Selling Expense", "expense", "debit"),
    "6200": ("Sales Commission Expense", "expense", "debit"),
}


def money(value):
    return Decimal(value or 0).quantize(CENT)


def ensure_default_accounts():
    accounts = {}
    for code, (name, account_type, normal_balance) in ACCOUNT_SPECS.items():
        account, _ = ChartOfAccount.objects.get_or_create(
            code=code,
            defaults={
                "name": name,
                "account_type": account_type,
                "normal_balance": normal_balance,
                "is_system": True,
            },
        )
        # Keep system account names/types aligned even if a migration created them first.
        changed = []
        if account.name != name:
            account.name = name
            changed.append("name")
        if account.account_type != account_type:
            account.account_type = account_type
            changed.append("account_type")
        if account.normal_balance != normal_balance:
            account.normal_balance = normal_balance
            changed.append("normal_balance")
        if not account.is_system:
            account.is_system = True
            changed.append("is_system")
        if changed:
            account.save(update_fields=changed)
        accounts[code] = account
    return accounts


def line(code, *, debit=ZERO, credit=ZERO, description="", party_type="", party_id=None, party_name=""):
    return {
        "code": str(code),
        "debit": money(debit),
        "credit": money(credit),
        "description": description or "",
        "party_type": party_type or "",
        "party_id": party_id,
        "party_name": party_name or "",
    }


@transaction.atomic
def post_entry(*, source_key, entry_date, reference="", memo="", source_type="", source_id=None, created_by=None, lines=None, module="egg_pos"):
    lines = [row for row in (lines or []) if money(row.get("debit")) != ZERO or money(row.get("credit")) != ZERO]
    if not lines:
        JournalEntry.objects.filter(source_key=source_key).delete()
        return None

    debit_total = money(sum((money(row.get("debit")) for row in lines), ZERO))
    credit_total = money(sum((money(row.get("credit")) for row in lines), ZERO))
    if debit_total != credit_total:
        raise ValueError(
            f"Journal entry {source_key} is not balanced: debit {debit_total} != credit {credit_total}."
        )

    accounts = ensure_default_accounts()
    missing = sorted({row["code"] for row in lines if row["code"] not in accounts})
    if missing:
        # Allow future non-system accounts that already exist in the chart.
        for account in ChartOfAccount.objects.filter(code__in=missing):
            accounts[account.code] = account
        still_missing = [code for code in missing if code not in accounts]
        if still_missing:
            raise ValueError(f"Unknown account code(s): {', '.join(still_missing)}")

    JournalEntry.objects.filter(source_key=source_key).delete()
    entry = JournalEntry.objects.create(
        entry_date=entry_date,
        reference=(reference or "")[:120],
        memo=(memo or "")[:255],
        module=module,
        source_type=source_type or "",
        source_id=source_id,
        source_key=source_key,
        created_by=created_by,
    )
    JournalLine.objects.bulk_create([
        JournalLine(
            entry=entry,
            account=accounts[row["code"]],
            description=(row.get("description") or "")[:255],
            debit=money(row.get("debit")),
            credit=money(row.get("credit")),
            party_type=row.get("party_type") or "",
            party_id=row.get("party_id"),
            party_name=(row.get("party_name") or "")[:180],
        )
        for row in lines
    ])
    return entry


def raw_account_balance(code, *, as_of=None, start_date=None, party_type=None, party_id=None):
    qs = JournalLine.objects.filter(account__code=str(code), entry__module="egg_pos")
    if as_of:
        qs = qs.filter(entry__entry_date__lte=as_of)
    if start_date:
        qs = qs.filter(entry__entry_date__gte=start_date)
    if party_type is not None:
        qs = qs.filter(party_type=party_type)
    if party_id is not None:
        qs = qs.filter(party_id=party_id)
    totals = qs.aggregate(debit=Sum("debit"), credit=Sum("credit"))
    return money(Decimal(totals["debit"] or 0) - Decimal(totals["credit"] or 0))


def normal_account_balance(code, *, as_of=None, start_date=None, party_type=None, party_id=None):
    ensure_default_accounts()
    account = ChartOfAccount.objects.get(code=str(code))
    raw = raw_account_balance(
        code,
        as_of=as_of,
        start_date=start_date,
        party_type=party_type,
        party_id=party_id,
    )
    return raw if account.normal_balance == "debit" else money(-raw)


def staff_cash_balance(user, *, as_of=None):
    return raw_account_balance("1040", as_of=as_of, party_type="user", party_id=user.id)


def supplier_balance(supplier, *, as_of=None):
    return normal_account_balance("2000", as_of=as_of, party_type="supplier", party_id=supplier.id)


def customer_balance(customer, *, as_of=None):
    return raw_account_balance("1100", as_of=as_of, party_type="customer", party_id=customer.id)


def staff_reimbursement_balance(user, *, as_of=None):
    return normal_account_balance("2020", as_of=as_of, party_type="user", party_id=user.id)


def staff_commission_balance(user, *, as_of=None):
    return normal_account_balance("2030", as_of=as_of, party_type="user", party_id=user.id)


def account_activity(account, *, start_date=None, end_date=None):
    qs = JournalLine.objects.filter(account=account, entry__module="egg_pos").select_related("entry").order_by("entry__entry_date", "entry_id", "id")
    if start_date:
        qs = qs.filter(entry__entry_date__gte=start_date)
    if end_date:
        qs = qs.filter(entry__entry_date__lte=end_date)
    return qs


def party_activity(*, party_type, party_id, start_date=None, end_date=None, account_codes=None):
    qs = JournalLine.objects.filter(
        entry__module="egg_pos",
        party_type=party_type,
        party_id=party_id,
    ).select_related("entry", "account").order_by("entry__entry_date", "entry_id", "id")
    if start_date:
        qs = qs.filter(entry__entry_date__gte=start_date)
    if end_date:
        qs = qs.filter(entry__entry_date__lte=end_date)
    if account_codes:
        qs = qs.filter(account__code__in=[str(code) for code in account_codes])
    return qs


def trial_balance(as_of):
    ensure_default_accounts()
    rows = []
    accounts = ChartOfAccount.objects.filter(is_active=True).order_by("code")
    for account in accounts:
        qs = JournalLine.objects.filter(
            account=account,
            entry__module="egg_pos",
            entry__entry_date__lte=as_of,
        )
        totals = qs.aggregate(debit=Sum("debit"), credit=Sum("credit"))
        raw = money(Decimal(totals["debit"] or 0) - Decimal(totals["credit"] or 0))
        if raw == ZERO:
            continue
        rows.append({
            "account": account,
            "debit": raw if raw > ZERO else ZERO,
            "credit": -raw if raw < ZERO else ZERO,
        })
    total_debit = money(sum((row["debit"] for row in rows), ZERO))
    total_credit = money(sum((row["credit"] for row in rows), ZERO))
    return rows, total_debit, total_credit


def income_statement(start_date, end_date):
    ensure_default_accounts()
    accounts = ChartOfAccount.objects.filter(
        account_type__in=["revenue", "cogs", "expense"],
        is_active=True,
    ).order_by("code")
    revenue_rows, cogs_rows, expense_rows = [], [], []
    for account in accounts:
        qs = JournalLine.objects.filter(
            account=account,
            entry__module="egg_pos",
            entry__entry_date__gte=start_date,
            entry__entry_date__lte=end_date,
        )
        totals = qs.aggregate(debit=Sum("debit"), credit=Sum("credit"))
        debit = Decimal(totals["debit"] or 0)
        credit = Decimal(totals["credit"] or 0)
        value = money(credit - debit) if account.account_type == "revenue" else money(debit - credit)
        if value == ZERO:
            continue
        row = {"account": account, "amount": value}
        if account.account_type == "revenue":
            revenue_rows.append(row)
        elif account.account_type == "cogs":
            cogs_rows.append(row)
        else:
            expense_rows.append(row)

    revenue = money(sum((row["amount"] for row in revenue_rows), ZERO))
    cogs = money(sum((row["amount"] for row in cogs_rows), ZERO))
    gross_profit = money(revenue - cogs)
    expenses = money(sum((row["amount"] for row in expense_rows), ZERO))
    net_profit = money(gross_profit - expenses)
    return {
        "revenue_rows": revenue_rows,
        "cogs_rows": cogs_rows,
        "expense_rows": expense_rows,
        "revenue": revenue,
        "cogs": cogs,
        "gross_profit": gross_profit,
        "expenses": expenses,
        "net_profit": net_profit,
    }
