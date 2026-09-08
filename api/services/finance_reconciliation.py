"""Shared read-only poultry finance calculations.

Both the live Finance Tracker and final Batch Report use this service.
Historical SaleRecord.cogs_allocated values are never rewritten here.
"""
from decimal import Decimal
from django.core.exceptions import PermissionDenied
from django.db.models import Sum, Q
from api.models.sensor import Batch, MortalityRecord
from api.models.sales import ChickCostEntry, SaleRecord, Expense
from api.models.investors import InvestorAllocation, FeedEntry, MedicineEntry
from api.models.eggs import EggProductionEntry, EggSale

def build_finance_data(user, status_filter="all", batch_ids=None):
    """
    Finance tracker with reconciled whole-bird ownership allocation.

    Financial values stay as Decimal values internally. The template rounds
    money to whole rupees for display, while calculations retain paisa-level
    precision.
    """

    is_admin = user.is_superuser or user.is_staff
    status_filter = (status_filter or "all").lower()

    if status_filter not in {"all", "active", "closed"}:
        status_filter = "all"

    # =========================================================
    # SMALL FINANCE HELPERS
    # =========================================================

    money_unit = Decimal("0.01")
    percent_unit = Decimal("0.1")
    zero_money = Decimal("0.00")

    def money(value):
        if value in (None, ""):
            value = 0
        return Decimal(value).quantize(money_unit)

    def money_share(value, ratio):
        return (money(value) * ratio).quantize(money_unit)

    def percentage(ratio):
        return (ratio * Decimal("100")).quantize(percent_unit)

    def owner_ratio(start_birds, batch_start_birds):
        if not batch_start_birds:
            return Decimal("0")
        return (
            Decimal(start_birds)
            / Decimal(batch_start_birds)
        )

    def allocate_whole_count(total_count, owners, batch_start_birds):
        """
        Allocate an integer bird count proportionally and reconcile the
        remainder so the owner totals always equal the batch total.
        """
        total_count = int(total_count or 0)

        if total_count <= 0 or batch_start_birds <= 0 or not owners:
            return [0] * len(owners)

        exact_values = []
        allocated_values = []

        for owner in owners:
            exact_value = (
                Decimal(total_count)
                * Decimal(owner["start_birds"])
                / Decimal(batch_start_birds)
            )
            exact_values.append(exact_value)
            allocated_values.append(int(exact_value))

        remaining = total_count - sum(allocated_values)

        # Largest-remainder method. For exact ties, keep owner order stable
        # (Admin/Farm is first, then investors by username).
        allocation_order = sorted(
            range(len(owners)),
            key=lambda index: (
                exact_values[index] - Decimal(allocated_values[index]),
                owners[index]["start_birds"],
                -index,
            ),
            reverse=True,
        )

        for index in allocation_order[:remaining]:
            allocated_values[index] += 1

        return allocated_values

    def allocate_money(value, owners, total_birds):
        amount = money(value)
        if not owners or total_birds <= 0:
            return [zero_money] * len(owners)
        cents = int((amount.copy_abs() / money_unit).to_integral_value())
        weights = [Decimal(o["start_birds"]) / Decimal(total_birds) for o in owners]
        exact = [Decimal(cents) * weight for weight in weights]
        portions = [int(number) for number in exact]
        missing = cents - sum(portions)
        order = sorted(range(len(owners)), key=lambda i: (exact[i] - portions[i], owners[i]["start_birds"], -i), reverse=True)
        for index in order[:missing]:
            portions[index] += 1
        sign = -1 if amount < 0 else 1
        return [Decimal(sign * number) * money_unit for number in portions]

    # =========================================================
    # GET BATCHES
    # =========================================================

    if is_admin:
        batches = Batch.objects.all()
    else:
        if not hasattr(user, "investor_profile"):
            raise PermissionDenied("You do not have access to poultry finance.")

        investor_batch_ids = InvestorAllocation.objects.filter(
            investor=user.investor_profile
        ).values_list("batch_id", flat=True)

        batches = Batch.objects.filter(id__in=investor_batch_ids)

    if batch_ids is not None:
        batches = batches.filter(pk__in=batch_ids)

    if status_filter == "active":
        batches = batches.filter(
            is_active=True,
            status="active",
        )
    elif status_filter == "closed":
        batches = batches.filter(
            Q(is_active=False)
            | Q(status__in=["sold", "closed"])
        )

    batches = batches.select_related("shed").order_by(
        "-is_active",
        "-start_date",
        "-id",
    )

    finance_rows = []

    # =========================================================
    # PAGE OVERVIEW TOTALS
    # =========================================================

    overview = {
        "starting_birds": 0,
        "mortality": 0,
        "sold": 0,
        "current_birds": 0,
        "net_sales": zero_money,
        "egg_sales": zero_money,
        "realized_cogs": zero_money,
        "realized_expenses": zero_money,
        "net_income": zero_money,
        "investment": zero_money,
        "recorded_cogs": zero_money,
        "recorded_expenses": zero_money,
        "active_batches": 0,
    }

    # =========================================================
    # PROCESS EACH BATCH
    # =========================================================

    for batch in batches:
        batch_start_birds = int(batch.bird_count_initial or 0)

        # -----------------------------------------------------
        # MORTALITY + SALES
        # -----------------------------------------------------

        total_mortality = int(
            MortalityRecord.objects.filter(batch=batch).aggregate(
                total=Sum("count")
            )["total"]
            or 0
        )

        sales_records = list(
            SaleRecord.objects.filter(batch=batch).order_by(
                "-sale_date",
                "-id",
            )
        )

        total_sold = sum(int(sale.birds_sold or 0) for sale in sales_records)

        total_sales_revenue = sum(
            (money(sale.total_amount) for sale in sales_records),
            zero_money,
        )
        gross_sales_revenue = sum(
            (money(sale.gross_amount) for sale in sales_records),
            zero_money,
        )
        total_discount = sum(
            (money(sale.discount_amount) for sale in sales_records),
            zero_money,
        )
        total_sale_weight = sum(
            (Decimal(sale.total_weight_kg or 0) for sale in sales_records),
            Decimal("0"),
        )

        average_sale_weight = (
            (total_sale_weight / Decimal(total_sold)).quantize(
                Decimal("0.001")
            )
            if total_sold > 0
            else Decimal("0.000")
        )

        average_sale_rate = (
            (gross_sales_revenue / total_sale_weight).quantize(money_unit)
            if total_sale_weight > 0
            else zero_money
        )

        # -----------------------------------------------------
        # EGG PRODUCTION + EGG SALES (LAYER SHED ONLY)
        # -----------------------------------------------------

        is_layer_batch = (
            getattr(batch.shed, "shed_type", "") == "layer"
        )

        egg_sales_records = []
        eggs_collected = 0
        damaged_eggs = 0
        usable_eggs = 0
        eggs_sold = 0
        egg_stock = 0
        egg_gross_sales_revenue = zero_money
        egg_discount = zero_money
        egg_net_sales = zero_money
        egg_sale_history = []

        if is_layer_batch:
            egg_production = EggProductionEntry.objects.filter(
                batch=batch
            ).aggregate(
                collected=Sum("eggs_collected"),
                damaged=Sum("damaged_eggs"),
            )

            eggs_collected = int(egg_production["collected"] or 0)
            damaged_eggs = int(egg_production["damaged"] or 0)
            usable_eggs = max(eggs_collected - damaged_eggs, 0)

            egg_sales_records = list(
                EggSale.objects.filter(batch=batch).order_by(
                    "-sale_date",
                    "-id",
                )
            )

            eggs_sold = sum(
                int(sale.eggs_sold or 0)
                for sale in egg_sales_records
            )
            egg_stock = max(usable_eggs - eggs_sold, 0)

            egg_gross_sales_revenue = money(
                sum(
                    (money(sale.gross_amount) for sale in egg_sales_records),
                    zero_money,
                )
            )
            egg_discount = money(
                sum(
                    (money(sale.discount_amount) for sale in egg_sales_records),
                    zero_money,
                )
            )
            egg_net_sales = money(
                sum(
                    (money(sale.total_amount) for sale in egg_sales_records),
                    zero_money,
                )
            )

            egg_sale_history = [
                {
                    "sale_date": sale.sale_date,
                    "buyer_name": sale.buyer_name,
                    "eggs_sold": int(sale.eggs_sold or 0),
                    "rate_per_egg": money(sale.rate_per_egg),
                    "gross_amount": money(sale.gross_amount),
                    "discount_amount": money(sale.discount_amount),
                    "total_amount": money(sale.total_amount),
                    "payment_method": sale.get_payment_method_display(),
                    "notes": sale.notes,
                }
                for sale in egg_sales_records
            ]

        current_birds = max(
            batch_start_birds - total_mortality - total_sold,
            0,
        )

        # -----------------------------------------------------
        # ALL EXPENSES
        # Every batch expense is part of COGS: fuel, labor, electricity,
        # transport, maintenance, rent, internet, service charges, misc, etc.
        # -----------------------------------------------------

        expenses = Expense.objects.filter(batch=batch)

        total_expenses = money(
            expenses.aggregate(total=Sum("amount"))["total"] or 0
        )

        expense_history = [
            {
                "expense_date": expense.expense_date,
                "category": expense.get_category_display(),
                "description": expense.description,
                "amount": money(expense.amount),
            }
            for expense in expenses.order_by(
                "-expense_date",
                "-id",
            )
        ]

        # -----------------------------------------------------
        # RECORDED COGS
        # -----------------------------------------------------

        chick_cost = money(
            ChickCostEntry.objects.filter(batch=batch).aggregate(
                total=Sum("chick_cost")
            )["total"]
            or 0
        )
        carriage_cost = money(
            ChickCostEntry.objects.filter(batch=batch).aggregate(
                total=Sum("carriage_cost")
            )["total"]
            or 0
        )
        feed_entries = list(
            FeedEntry.objects.filter(batch=batch).order_by(
                "-entry_date",
                "-id",
            )
        )

        feed_cost = money(
            sum(
                (money(entry.amount) for entry in feed_entries),
                zero_money,
            )
        )

        feed_history = [
            {
                "entry_date": entry.entry_date,
                "amount": money(entry.amount),
                "notes": entry.notes,
            }
            for entry in feed_entries
        ]
        medicine_entries = list(
            MedicineEntry.objects.filter(batch=batch).order_by(
                "-entry_date",
                "-id",
            )
        )

        medicine_cost = money(
            sum(
                (money(entry.amount) for entry in medicine_entries),
                zero_money,
            )
        )

        medicine_history = [
            {
                "entry_date": entry.entry_date,
                "medicine_name": entry.medicine_name,
                "medicine_type": entry.medicine_type,
                "medicine_type_display": entry.get_medicine_type_display(),
                "amount": money(entry.amount),
                "notes": entry.notes,
            }
            for entry in medicine_entries
        ]

        total_cogs = money(
            chick_cost
            + carriage_cost
            + feed_cost
            + medicine_cost
            + total_expenses
        )

        # -----------------------------------------------------
        # LAYER FINANCIAL POSITION TO DATE
        # -----------------------------------------------------

        total_batch_revenue = money(
            total_sales_revenue + egg_net_sales
        )

        layer_profit_to_date = money(
            total_batch_revenue - total_cogs
        )

        layer_roi_to_date = Decimal("0.0")
        if total_cogs > 0:
            layer_roi_to_date = (
                layer_profit_to_date
                / total_cogs
                * Decimal("100")
            ).quantize(percent_unit)

        # -----------------------------------------------------
        # REALIZED POSITION
        # -----------------------------------------------------

        batch_locked_cogs_total = money(
            sum(
                (money(sale.cogs_allocated) for sale in sales_records),
                zero_money,
            )
        )

        # All expenses are now part of COGS.  Realized profit therefore
        # subtracts only the COGS locked to birds at the time of each sale.
        batch_realized_expenses = zero_money

        batch_net_income = money(
            total_sales_revenue
            - batch_locked_cogs_total
        )

        batch_total_investment = money(
            batch_locked_cogs_total
        )

        batch_roi = Decimal("0.0")
        if batch_total_investment > 0:
            batch_roi = (
                batch_net_income
                / batch_total_investment
                * Decimal("100")
            ).quantize(percent_unit)

        remaining_cogs = max(
            total_cogs - batch_locked_cogs_total,
            zero_money,
        )

        # Current cost carried by each live bird.
        #
        # Before any sale:
        #   Total COGS / current live birds
        #
        # After sales have started, already-locked sale COGS is removed
        # first so sold birds are not charged again to the remaining flock.
        cost_per_live_bird = zero_money
        if current_birds > 0:
            cost_per_live_bird = money(
                remaining_cogs / Decimal(current_birds)
            )

        remaining_expenses = zero_money

        # Final-period accounting is different from sold-bird realization.
        # Do not rewrite historical cogs_allocated values to close a batch.
        is_final = not batch.is_active or batch.status in {"closed", "sold"}
        final_profit = layer_profit_to_date
        final_roi = layer_roi_to_date
        closing_cost_difference = money(total_cogs - batch_locked_cogs_total)
        closing_cost_adjustment = closing_cost_difference if current_birds == 0 else zero_money
        remaining_live_inventory_cost = closing_cost_difference if current_birds > 0 else zero_money
        reconciliation_warnings = []
        if batch_start_birds != total_mortality + total_sold + current_birds:
            reconciliation_warnings.append("Bird quantities do not reconcile. Check mortality and sales.")
        if is_final and current_birds > 0:
            reconciliation_warnings.append("This batch is closed but still has recorded live birds. Confirm their disposal or transfer before treating the result as a fully settled closure.")
        if is_final and egg_stock > 0:
            reconciliation_warnings.append("Unsold eggs remain in stock. Their value is not separately capitalized in this report.")
        if batch_locked_cogs_total > total_cogs:
            reconciliation_warnings.append("Locked sale COGS exceed all currently recorded batch costs. Review historical sale snapshots and cost entries.")
        if is_final and abs(closing_cost_difference) >= money_unit:
            reconciliation_warnings.append("Recorded COGS and historical sale-locked COGS differ. The final result includes all recorded costs without modifying saved sale snapshots.")

        # -----------------------------------------------------
        # OWNERSHIP INPUTS
        # -----------------------------------------------------

        allocations = list(
            InvestorAllocation.objects.filter(batch=batch)
            .select_related("investor__user")
            .order_by("investor__user__username", "id")
        )

        allocated_investor_birds = sum(
            int(allocation.birds_owned or 0)
            for allocation in allocations
        )

        admin_birds = max(
            batch_start_birds - allocated_investor_birds,
            0,
        )

        all_owner_rows = []

        if admin_birds > 0:
            all_owner_rows.append({
                "kind": "admin",
                "allocation_id": None,
                "name": "Admin / Farm",
                "start_birds": admin_birds,
                "is_current_user": is_admin,
            })

        for allocation in allocations:
            investor_user = allocation.investor.user
            investor_name = (
                investor_user.get_full_name().strip()
                or investor_user.username
            )

            all_owner_rows.append({
                "kind": "investor",
                "allocation_id": allocation.id,
                "investor_id": allocation.investor_id,
                "name": investor_name,
                "start_birds": int(allocation.birds_owned or 0),
                "is_current_user": (
                    not is_admin
                    and hasattr(user, "investor_profile")
                    and allocation.investor_id
                    == user.investor_profile.id
                ),
            })

        mortality_allocations = allocate_whole_count(
            total_mortality,
            all_owner_rows,
            batch_start_birds,
        )
        sold_allocations = allocate_whole_count(
            total_sold,
            all_owner_rows,
            batch_start_birds,
        )

        # -----------------------------------------------------
        # BATCH SALE HISTORY (admin summary)
        # -----------------------------------------------------

        sale_history = []

        for sale in sales_records:
            sale_history.append({
                "sale_date": sale.sale_date,
                "birds_sold": int(sale.birds_sold or 0),
                "total_weight_kg": sale.total_weight_kg,
                "rate_per_kg": money(sale.rate_per_kg),
                "gross_amount": money(sale.gross_amount),
                "discount_amount": money(sale.discount_amount),
                "total_amount": money(sale.total_amount),
                "cogs_allocated": money(sale.cogs_allocated),
                "gross_profit": money(
                    money(sale.total_amount) - money(sale.cogs_allocated)
                ),
                "notes": sale.notes,
            })

        # Allocate every cost transaction to the paisa, so each owner's
        # history reconciles with both the category total and the batch.
        feed_entry_shares = [
            allocate_money(entry["amount"], all_owner_rows, batch_start_birds)
            for entry in feed_history
        ]
        medicine_entry_shares = [
            allocate_money(entry["amount"], all_owner_rows, batch_start_birds)
            for entry in medicine_history
        ]
        expense_entry_shares = [
            allocate_money(entry["amount"], all_owner_rows, batch_start_birds)
            for entry in expense_history
        ]

        owner_amounts = {
            key: allocate_money(amount, all_owner_rows, batch_start_birds)
            for key, amount in {
                "chick_cost": chick_cost,
                "carriage_cost": carriage_cost,
                "feed_cost": feed_cost,
                "medicine_cost": medicine_cost,
                "expense_share": total_expenses,
            }.items()
        }

        # -----------------------------------------------------
        # OWNER FINANCE ROWS
        # -----------------------------------------------------

        for index, owner in enumerate(all_owner_rows):
            ratio = owner_ratio(owner["start_birds"], batch_start_birds)

            owner["share_ratio"] = ratio
            owner["percentage"] = percentage(ratio)
            owner["mortality"] = mortality_allocations[index]
            owner["sold"] = sold_allocations[index]
            owner["current_birds"] = max(
                owner["start_birds"]
                - owner["mortality"]
                - owner["sold"],
                0,
            )
            owner["weight_sold"] = (
                total_sale_weight * ratio
            ).quantize(Decimal("0.01"))

            owner["gross_revenue"] = money_share(
                gross_sales_revenue,
                ratio,
            )
            owner["discount_share"] = money_share(
                total_discount,
                ratio,
            )
            owner["revenue"] = money_share(
                total_sales_revenue,
                ratio,
            )

            owner["egg_gross_revenue"] = money_share(
                egg_gross_sales_revenue,
                ratio,
            )
            owner["egg_discount_share"] = money_share(
                egg_discount,
                ratio,
            )
            owner["egg_revenue"] = money_share(
                egg_net_sales,
                ratio,
            )
            owner["total_revenue"] = money(
                owner["revenue"] + owner["egg_revenue"]
            )

            for field, amounts in owner_amounts.items():
                owner[field] = amounts[index]
            owner["recorded_cogs"] = money(sum(
                (owner[field] for field in (
                    "chick_cost", "carriage_cost", "feed_cost",
                    "medicine_cost", "expense_share",
                )), zero_money
            ))
            owner["locked_cogs"] = zero_money  # filled from saved sale histories below
            owner["remaining_cogs"] = zero_money
            owner["cost_per_live_bird"] = zero_money

            owner["realized_expenses"] = zero_money
            owner["remaining_expenses"] = zero_money

            owner["net_income"] = money(
                owner["revenue"]
                - owner["locked_cogs"]
            )
            owner["investment"] = money(
                owner["locked_cogs"]
            )
            owner["roi"] = Decimal("0.0")

            if owner["investment"] > 0:
                owner["roi"] = (
                    owner["net_income"]
                    / owner["investment"]
                    * Decimal("100")
                ).quantize(percent_unit)

            owner["layer_profit_to_date"] = money(
                owner["total_revenue"] - owner["recorded_cogs"]
            )
            owner["layer_roi_to_date"] = Decimal("0.0")

            if owner["recorded_cogs"] > 0:
                owner["layer_roi_to_date"] = (
                    owner["layer_profit_to_date"]
                    / owner["recorded_cogs"]
                    * Decimal("100")
                ).quantize(percent_unit)

            # Owner-level histories.  Feed, medicine and every expense
            # show the owner's precise ownership share of each entry.
            owner["sale_history"] = []
            owner["egg_sale_history"] = []
            owner["feed_history"] = [
                {
                    "entry_date": entry["entry_date"],
                    "notes": entry["notes"],
                    "amount": feed_entry_shares[entry_index][index],
                }
                for entry_index, entry in enumerate(feed_history)
            ]
            owner["medicine_history"] = [
                {
                    "entry_date": entry["entry_date"],
                    "medicine_name": entry["medicine_name"],
                    "medicine_type_display": entry["medicine_type_display"],
                    "notes": entry["notes"],
                    "amount": medicine_entry_shares[entry_index][index],
                }
                for entry_index, entry in enumerate(medicine_history)
            ]
            owner["expense_history"] = [
                {
                    "expense_date": entry["expense_date"],
                    "category": entry["category"],
                    "description": entry["description"],
                    "amount": expense_entry_shares[entry_index][index],
                }
                for entry_index, entry in enumerate(expense_history)
            ]

        for sale in sales_records:
            per_sale_birds = allocate_whole_count(
                int(sale.birds_sold or 0), all_owner_rows, batch_start_birds,
            )
            gross_amounts = allocate_money(sale.gross_amount, all_owner_rows, batch_start_birds)
            discount_amounts = allocate_money(sale.discount_amount, all_owner_rows, batch_start_birds)
            net_amounts = [money(g - d) for g, d in zip(gross_amounts, discount_amounts)]
            locked_amounts = allocate_money(sale.cogs_allocated, all_owner_rows, batch_start_birds)
            for index, owner in enumerate(all_owner_rows):
                owner_net_revenue = net_amounts[index]
                owner_sale_cogs = locked_amounts[index]
                owner["sale_history"].append({
                    "sale_date": sale.sale_date,
                    "birds_sold": per_sale_birds[index],
                    "weight_sold": (Decimal(sale.total_weight_kg or 0) * owner["share_ratio"]).quantize(Decimal("0.01")),
                    "rate_per_kg": money(sale.rate_per_kg),
                    "gross_revenue": gross_amounts[index],
                    "discount": discount_amounts[index],
                    "net_revenue": owner_net_revenue,
                    "locked_cogs": owner_sale_cogs,
                    "sale_margin": money(owner_net_revenue - owner_sale_cogs),
                })

        if is_layer_batch:
            for egg_sale in egg_sales_records:
                per_sale_eggs = allocate_whole_count(
                    int(egg_sale.eggs_sold or 0), all_owner_rows, batch_start_birds,
                )
                gross_amounts = allocate_money(egg_sale.gross_amount, all_owner_rows, batch_start_birds)
                discount_amounts = allocate_money(egg_sale.discount_amount, all_owner_rows, batch_start_birds)
                net_amounts = [money(g - d) for g, d in zip(gross_amounts, discount_amounts)]
                for index, owner in enumerate(all_owner_rows):
                    owner["egg_sale_history"].append({
                        "sale_date": egg_sale.sale_date,
                        "buyer_name": egg_sale.buyer_name,
                        "eggs_sold": per_sale_eggs[index],
                        "rate_per_egg": money(egg_sale.rate_per_egg),
                        "gross_revenue": gross_amounts[index],
                        "discount": discount_amounts[index],
                        "net_revenue": net_amounts[index],
                        "payment_method": egg_sale.get_payment_method_display(),
                        "notes": egg_sale.notes,
                    })

        # Make aggregate monetary figures equal the sum of the actual
        # owner invoice histories and cost categories, to the paisa.
        for owner in all_owner_rows:
            owner["feed_cost"] = money(sum((h["amount"] for h in owner["feed_history"]), zero_money))
            owner["medicine_cost"] = money(sum((h["amount"] for h in owner["medicine_history"]), zero_money))
            owner["expense_share"] = money(sum((h["amount"] for h in owner["expense_history"]), zero_money))
            owner["recorded_cogs"] = money(sum((owner[field] for field in (
                "chick_cost", "carriage_cost", "feed_cost", "medicine_cost", "expense_share",
            )), zero_money))
            owner["gross_revenue"] = money(sum((h["gross_revenue"] for h in owner["sale_history"]), zero_money))
            owner["discount_share"] = money(sum((h["discount"] for h in owner["sale_history"]), zero_money))
            owner["revenue"] = money(sum((h["net_revenue"] for h in owner["sale_history"]), zero_money))
            owner["locked_cogs"] = money(sum((h["locked_cogs"] for h in owner["sale_history"]), zero_money))
            owner["egg_gross_revenue"] = money(sum((h["gross_revenue"] for h in owner["egg_sale_history"]), zero_money))
            owner["egg_discount_share"] = money(sum((h["discount"] for h in owner["egg_sale_history"]), zero_money))
            owner["egg_revenue"] = money(sum((h["net_revenue"] for h in owner["egg_sale_history"]), zero_money))
            owner["total_revenue"] = money(owner["revenue"] + owner["egg_revenue"])
            owner["remaining_cogs"] = max(owner["recorded_cogs"] - owner["locked_cogs"], zero_money)
            owner["cost_per_live_bird"] = money(owner["remaining_cogs"] / Decimal(owner["current_birds"])) if owner["current_birds"] > 0 else zero_money
            owner["net_income"] = money(owner["revenue"] - owner["locked_cogs"])
            owner["investment"] = owner["locked_cogs"]
            owner["roi"] = (owner["net_income"] / owner["investment"] * Decimal("100")).quantize(percent_unit) if owner["investment"] > 0 else Decimal("0.0")
            owner["layer_profit_to_date"] = money(owner["total_revenue"] - owner["recorded_cogs"])
            owner["layer_roi_to_date"] = (owner["layer_profit_to_date"] / owner["recorded_cogs"] * Decimal("100")).quantize(percent_unit) if owner["recorded_cogs"] > 0 else Decimal("0.0")
            owner["final_profit"] = owner["layer_profit_to_date"]
            owner["final_roi"] = owner["layer_roi_to_date"]
            owner["closing_cost_adjustment"] = money(owner["recorded_cogs"] - owner["locked_cogs"]) if current_birds == 0 else zero_money
            owner["remaining_live_inventory_cost"] = money(owner["recorded_cogs"] - owner["locked_cogs"]) if current_birds > 0 else zero_money

        # Investor login sees only that investor's ownership row. Admin sees all.
        if is_admin:
            visible_owner_rows = all_owner_rows
        else:
            visible_owner_rows = [
                owner
                for owner in all_owner_rows
                if owner.get("is_current_user")
            ]

        current_user_owner = (
            visible_owner_rows[0]
            if not is_admin and visible_owner_rows
            else None
        )

        # -----------------------------------------------------
        # OVERVIEW TOTALS
        # -----------------------------------------------------

        if is_admin:
            overview["starting_birds"] += batch_start_birds
            overview["mortality"] += total_mortality
            overview["sold"] += total_sold
            overview["current_birds"] += current_birds
            overview["net_sales"] += total_sales_revenue
            overview["egg_sales"] += egg_net_sales
            overview["realized_cogs"] += batch_locked_cogs_total
            overview["net_income"] += batch_net_income
            overview["investment"] += batch_total_investment
            overview["recorded_cogs"] += total_cogs
            overview["recorded_expenses"] += total_expenses
        elif current_user_owner:
            overview["starting_birds"] += current_user_owner["start_birds"]
            overview["mortality"] += current_user_owner["mortality"]
            overview["sold"] += current_user_owner["sold"]
            overview["current_birds"] += current_user_owner["current_birds"]
            overview["net_sales"] += current_user_owner["revenue"]
            overview["egg_sales"] += current_user_owner["egg_revenue"]
            overview["realized_cogs"] += current_user_owner["locked_cogs"]
            overview["net_income"] += current_user_owner["net_income"]
            overview["investment"] += current_user_owner["investment"]
            overview["recorded_cogs"] += current_user_owner["recorded_cogs"]
            overview["recorded_expenses"] += current_user_owner[
                "expense_share"
            ]

        if batch.is_active and batch.status == "active":
            overview["active_batches"] += 1

        # -----------------------------------------------------
        # SEND BATCH ROW
        # -----------------------------------------------------

        finance_rows.append({
            "batch": batch,
            "is_final": is_final,
            "final_profit": final_profit,
            "final_roi": final_roi,
            "closing_cost_difference": closing_cost_difference,
            "closing_cost_adjustment": closing_cost_adjustment,
            "remaining_live_inventory_cost": remaining_live_inventory_cost,
            "reconciliation_warnings": reconciliation_warnings,
            "all_owner_rows": all_owner_rows,
            "status_display": batch.get_status_display(),
            "status_key": batch.status,
            "is_layer_batch": is_layer_batch,
            "current_birds": current_birds,
            "total_mortality": total_mortality,
            "total_sold": total_sold,
            "gross_sales_revenue": gross_sales_revenue,
            "total_discount": total_discount,
            "total_sales_revenue": total_sales_revenue,
            "total_sale_weight": total_sale_weight.quantize(
                Decimal("0.01")
            ),
            "average_sale_weight": average_sale_weight,
            "average_sale_rate": average_sale_rate,
            "eggs_collected": eggs_collected,
            "damaged_eggs": damaged_eggs,
            "usable_eggs": usable_eggs,
            "eggs_sold": eggs_sold,
            "egg_stock": egg_stock,
            "egg_gross_sales_revenue": egg_gross_sales_revenue,
            "egg_discount": egg_discount,
            "egg_net_sales": egg_net_sales,
            "total_batch_revenue": total_batch_revenue,
            "layer_profit_to_date": layer_profit_to_date,
            "layer_roi_to_date": layer_roi_to_date,
            "egg_sale_history": egg_sale_history,
            "chick_cost": chick_cost,
            "cost_per_live_bird": cost_per_live_bird,
            "carriage_cost": carriage_cost,
            "feed_cost": feed_cost,
            "feed_history": feed_history,
            "feed_purchase_count": len(feed_history),
            "medicine_cost": medicine_cost,
            "medicine_history": medicine_history,
            "medicine_purchase_count": len(medicine_history),
            "expense_cost": total_expenses,
            "expense_history": expense_history,
            "expense_count": len(expense_history),
            "total_cogs": total_cogs,
            "total_expenses": total_expenses,
            "batch_locked_cogs_total": batch_locked_cogs_total,
            "batch_realized_expenses": batch_realized_expenses,
            "remaining_cogs": remaining_cogs,
            "remaining_expenses": remaining_expenses,
            "batch_net_income": batch_net_income,
            "batch_total_investment": batch_total_investment,
            "batch_roi": batch_roi,
            "owner_rows": visible_owner_rows,
            "all_owner_count": len(all_owner_rows),
            "current_user_owner": current_user_owner,
            "sale_history": sale_history,
        })
        if not is_admin:
            finance_rows[-1]["all_owner_rows"] = visible_owner_rows

    overview["net_sales"] = money(overview["net_sales"])
    overview["egg_sales"] = money(overview["egg_sales"])
    overview["realized_cogs"] = money(overview["realized_cogs"])
    overview["realized_expenses"] = money(overview["realized_expenses"])
    overview["net_income"] = money(overview["net_income"])
    overview["investment"] = money(overview["investment"])
    overview["recorded_cogs"] = money(overview["recorded_cogs"])
    overview["recorded_expenses"] = money(overview["recorded_expenses"])
    overview["total_revenue"] = money(overview["net_sales"] + overview["egg_sales"])
    overview["whole_batch_profit"] = money(overview["total_revenue"] - overview["recorded_cogs"])
    overview["whole_batch_roi"] = (
        overview["whole_batch_profit"] / overview["recorded_cogs"] * Decimal("100")
    ).quantize(percent_unit) if overview["recorded_cogs"] > 0 else Decimal("0.0")
    overview["roi"] = Decimal("0.0")

    if overview["investment"] > 0:
        overview["roi"] = (
            overview["net_income"]
            / overview["investment"]
            * Decimal("100")
        ).quantize(percent_unit)

    return {
        "finance_rows": finance_rows,
        "is_admin": is_admin,
        "overview": overview,
        "status_filter": status_filter,
    }


def build_report_position(row, owner=None):
    """Map one canonical batch/owner snapshot to a report position."""
    if owner is None:
        return {
            "start_birds": row["batch"].bird_count_initial,
            "current_birds": row["current_birds"],
            "mortality": row["total_mortality"], "sold": row["total_sold"],
            "revenue": row["total_sales_revenue"], "egg_revenue": row["egg_net_sales"],
            "total_revenue": row["total_batch_revenue"],
            "chick_cost": row["chick_cost"], "carriage_cost": row["carriage_cost"],
            "feed_cost": row["feed_cost"], "medicine_cost": row["medicine_cost"],
            "expenses": row["expense_cost"], "total_cogs": row["total_cogs"],
            "locked_cogs": row["batch_locked_cogs_total"],
            "net_income": row["final_profit"], "roi": row["final_roi"],
            "realized_profit": row["batch_net_income"], "realized_roi": row["batch_roi"],
            "closing_cost_adjustment": row["closing_cost_adjustment"],
            "remaining_live_inventory_cost": row["remaining_live_inventory_cost"],
            "warnings": row["reconciliation_warnings"],
        }
    return {
        "start_birds": owner["start_birds"], "current_birds": owner["current_birds"],
        "mortality": owner["mortality"], "sold": owner["sold"],
        "revenue": owner["revenue"], "egg_revenue": owner["egg_revenue"],
        "total_revenue": owner["total_revenue"],
        "chick_cost": owner["chick_cost"], "carriage_cost": owner["carriage_cost"],
        "feed_cost": owner["feed_cost"], "medicine_cost": owner["medicine_cost"],
        "expenses": owner["expense_share"], "total_cogs": owner["recorded_cogs"],
        "locked_cogs": owner["locked_cogs"],
        "net_income": owner["final_profit"], "roi": owner["final_roi"],
        "realized_profit": owner["net_income"], "realized_roi": owner["roi"],
        "closing_cost_adjustment": owner["closing_cost_adjustment"],
        "remaining_live_inventory_cost": owner["remaining_live_inventory_cost"],
        "warnings": row["reconciliation_warnings"],
    }
