from decimal import Decimal, ROUND_HALF_UP

from django.db import migrations


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
    "3000": ("Opening Balance / Owner Equity", "equity", "credit"),
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
    return Decimal(value or 0).quantize(CENT, rounding=ROUND_HALF_UP)


def forward(apps, schema_editor):
    Account = apps.get_model("api", "ChartOfAccount")
    Entry = apps.get_model("api", "JournalEntry")
    Line = apps.get_model("api", "JournalLine")
    Purchase = apps.get_model("api", "EggPOSPurchase")
    PurchaseItem = apps.get_model("api", "EggPOSPurchaseItem")
    FarmTransfer = apps.get_model("api", "EggPOSFarmTransfer")
    FarmItem = apps.get_model("api", "EggPOSFarmTransferItem")
    FarmPayment = apps.get_model("api", "EggPOSFarmTransferPayment")
    Sale = apps.get_model("api", "EggPOSSale")
    SalePayment = apps.get_model("api", "EggPOSSalePayment")
    Customer = apps.get_model("api", "EggPOSCustomer")
    Supplier = apps.get_model("api", "EggPOSSupplier")
    Access = apps.get_model("api", "EggPOSUserAccess")
    User = apps.get_model("auth", "User")

    account_ids = {}
    for code, (name, account_type, normal_balance) in ACCOUNT_SPECS.items():
        account, _ = Account.objects.get_or_create(
            code=code,
            defaults={
                "name": name,
                "account_type": account_type,
                "normal_balance": normal_balance,
                "is_system": True,
                "is_active": True,
            },
        )
        account_ids[code] = account.id

    def add_entry(source_key, entry_date, reference, memo, source_type, source_id, created_by_id, rows):
        if Entry.objects.filter(source_key=source_key).exists():
            return
        debit = money(sum((money(row.get("debit")) for row in rows), ZERO))
        credit = money(sum((money(row.get("credit")) for row in rows), ZERO))
        if debit != credit:
            raise RuntimeError(f"Unbalanced Egg POS backfill {source_key}: {debit} != {credit}")
        entry = Entry.objects.create(
            entry_date=entry_date,
            reference=(reference or "")[:120],
            memo=(memo or "")[:255],
            module="egg_pos",
            source_type=source_type,
            source_id=source_id,
            source_key=source_key,
            created_by_id=created_by_id,
        )
        Line.objects.bulk_create([
            Line(
                entry_id=entry.id,
                account_id=account_ids[row["code"]],
                description=(row.get("description") or "")[:255],
                debit=money(row.get("debit")),
                credit=money(row.get("credit")),
                party_type=row.get("party_type") or "",
                party_id=row.get("party_id"),
                party_name=(row.get("party_name") or "")[:180],
            )
            for row in rows
            if money(row.get("debit")) != ZERO or money(row.get("credit")) != ZERO
        ])

    suppliers = {row.id: row for row in Supplier.objects.all()}
    customers = {row.id: row for row in Customer.objects.all()}
    users = {row.id: row for row in User.objects.all()}
    access = {row.user_id: row for row in Access.objects.all()}

    for purchase in Purchase.objects.all().order_by("id").iterator():
        items = PurchaseItem.objects.filter(purchase_id=purchase.id)
        base = money(sum((Decimal(item.quantity or 0) * Decimal(item.unit_cost or 0) for item in items), ZERO))
        transport = money(getattr(purchase, "transport_cost", ZERO))
        landed = money(base + transport)
        supplier = suppliers.get(purchase.supplier_id)
        party_name = getattr(supplier, "name", "Supplier")
        rows = [
            {"code": "1200", "debit": landed, "description": "Egg inventory received"},
            {"code": "2000", "credit": base, "description": "Supplier invoice", "party_type": "supplier", "party_id": purchase.supplier_id, "party_name": party_name},
        ]
        if transport > ZERO:
            if purchase.transport_payment_method == "supplier_payable":
                rows.append({"code": "2000", "credit": transport, "description": "Inbound transport added to supplier payable", "party_type": "supplier", "party_id": purchase.supplier_id, "party_name": party_name})
            else:
                code = "1010" if purchase.transport_payment_method == "bank_transfer" else "1000"
                rows.append({"code": code, "credit": transport, "description": "Inbound transport capitalized into inventory"})
        add_entry(
            f"egg_pos:purchase:{purchase.id}", purchase.purchase_date, f"PUR-{purchase.id:05d}",
            f"Egg purchase from {party_name}", "egg_pos_purchase", purchase.id, purchase.created_by_id, rows,
        )

    for transfer in FarmTransfer.objects.all().order_by("id").iterator():
        items = FarmItem.objects.filter(transfer_id=transfer.id)
        base = money(sum((Decimal(item.quantity or 0) * Decimal(item.unit_cost or 0) for item in items), ZERO))
        transport = money(getattr(transfer, "transport_cost", ZERO))
        landed = money(base + transport)
        rows = [
            {"code": "1200", "debit": landed, "description": "Farm eggs received into POS inventory"},
            {"code": "2010", "credit": base, "description": "Internal payable to RayNoor Egg Production", "party_type": "farm", "party_id": transfer.batch_id, "party_name": "RayNoor Egg Production"},
        ]
        if transport > ZERO:
            code = "1010" if transfer.transport_payment_method == "bank_transfer" else "1000"
            rows.append({"code": code, "credit": transport, "description": "Inbound transport capitalized into inventory"})
        add_entry(
            f"egg_pos:farm_transfer:{transfer.id}", transfer.transfer_date,
            transfer.transfer_number or f"RNET-{transfer.id:05d}", "Farm to Egg POS internal inventory transfer",
            "egg_pos_farm_transfer", transfer.id, transfer.created_by_id, rows,
        )

    for payment in FarmPayment.objects.all().order_by("id").iterator():
        transfer = FarmTransfer.objects.filter(id=payment.transfer_id).first()
        batch_id = transfer.batch_id if transfer else None
        code = "1010" if payment.payment_method in {"bank_transfer", "easypaisa", "jazzcash"} else "1000"
        rows = [
            {"code": "2010", "debit": payment.amount, "description": "Farm payable settled", "party_type": "farm", "party_id": batch_id, "party_name": "RayNoor Egg Production"},
            {"code": code, "credit": payment.amount, "description": "Payment made"},
        ]
        add_entry(
            f"egg_pos:farm_transfer_payment:{payment.id}", payment.payment_date,
            payment.reference or f"RN-PAY-{payment.id:05d}", "Payment to RayNoor Egg Production",
            "egg_pos_farm_transfer_payment", payment.id, payment.recorded_by_id, rows,
        )

    for sale in Sale.objects.all().order_by("id").iterator():
        customer = customers.get(sale.customer_id)
        if customer:
            name = f"{customer.name} — {customer.company_name}" if customer.company_name else customer.name
        else:
            name = sale.customer_name or "Walk-in Customer"
        party = {"party_type": "customer", "party_id": sale.customer_id, "party_name": name}
        rows = [
            {"code": "1100", "debit": sale.net_total, "description": "Customer invoice", **party},
            {"code": "4000", "credit": sale.net_total, "description": "Egg sales revenue"},
            {"code": "5000", "debit": sale.cogs_total, "description": "FIFO egg cost of goods sold"},
            {"code": "1200", "credit": sale.cogs_total, "description": "FIFO inventory consumed"},
        ]
        seller = users.get(sale.created_by_id)
        seller_name = (f"{seller.first_name} {seller.last_name}".strip() or seller.username) if seller else "staff"
        add_entry(
            f"egg_pos:sale:{sale.id}", sale.sale_date, sale.sale_number or f"RNE-{sale.id:05d}",
            f"Egg sale by {seller_name}", "egg_pos_sale", sale.id, sale.created_by_id, rows,
        )

    for payment in SalePayment.objects.all().order_by("id").iterator():
        sale = Sale.objects.filter(id=payment.sale_id).first()
        if not sale:
            continue
        customer = customers.get(sale.customer_id)
        if customer:
            name = f"{customer.name} — {customer.company_name}" if customer.company_name else customer.name
        else:
            name = sale.customer_name or "Walk-in Customer"
        customer_party = {"party_type": "customer", "party_id": sale.customer_id, "party_name": name}
        debit_code = "1000"
        debit_party = {}
        if payment.payment_method in {"bank_transfer", "easypaisa", "jazzcash"}:
            debit_code = "1010"
        elif payment.payment_method == "cash":
            user = users.get(payment.recorded_by_id)
            user_access = access.get(payment.recorded_by_id)
            if user and not user.is_superuser and not user.is_staff and user_access and user_access.is_active and user_access.can_sell and not user_access.can_manage:
                debit_code = "1040"
                display = f"{user.first_name} {user.last_name}".strip() or user.username
                debit_party = {"party_type": "user", "party_id": user.id, "party_name": display}
        rows = [
            {"code": debit_code, "debit": payment.amount, "description": "Customer payment received", **debit_party},
            {"code": "1100", "credit": payment.amount, "description": "Customer receivable settled", **customer_party},
        ]
        add_entry(
            f"egg_pos:sale_payment:{payment.id}", payment.payment_date,
            payment.reference or f"{sale.sale_number}-PAY-{payment.id}", f"Customer payment for {sale.sale_number}",
            "egg_pos_sale_payment", payment.id, payment.recorded_by_id, rows,
        )


def reverse(apps, schema_editor):
    Entry = apps.get_model("api", "JournalEntry")
    Entry.objects.filter(module="egg_pos").delete()


class Migration(migrations.Migration):
    dependencies = [("api", "0060_egg_pos_accounting_foundation")]
    operations = [migrations.RunPython(forward, reverse)]
