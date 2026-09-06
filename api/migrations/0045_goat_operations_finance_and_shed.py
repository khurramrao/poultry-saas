from decimal import Decimal

import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


def create_goat_shed_and_assign_device(apps, schema_editor):
    Shed = apps.get_model("api", "Shed")
    Device = apps.get_model("api", "Device")
    Goat = apps.get_model("api", "Goat")

    goat_shed = Shed.objects.filter(shed_type="goat").order_by("id").first()
    if goat_shed is None:
        goat_shed = Shed.objects.create(
            name="Shed 3",
            shed_type="goat",
        )

    Goat.objects.filter(shed__isnull=True).update(
        shed=goat_shed,
        shed_label=goat_shed.name,
    )

    # DEV-3 is the physical controller the farm uses for Goat Shed / Shed 3.
    Device.objects.filter(device_id="esp32_shed_3").update(shed=goat_shed)


def reverse_goat_shed_assignment(apps, schema_editor):
    # Do not automatically move physical devices back on reverse. A reverse
    # migration should not guess which poultry shed owned DEV-3 previously.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0044_goat_single_rn_id"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="shed",
            name="shed_type",
            field=models.CharField(
                choices=[
                    ("meat", "Meat"),
                    ("layer", "Layer"),
                    ("goat", "Goat"),
                ],
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="temperaturerule",
            name="shed_type",
            field=models.CharField(
                choices=[
                    ("meat", "Meat"),
                    ("layer", "Layer"),
                    ("goat", "Goat"),
                ],
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="goat",
            name="shed",
            field=models.ForeignKey(
                blank=True,
                limit_choices_to={"shed_type": "goat"},
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="goats",
                to="api.shed",
            ),
        ),
        migrations.CreateModel(
            name="GoatCostEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("category", models.CharField(choices=[("feed", "Feed"), ("medicine", "Medicine"), ("expense", "Expense")], max_length=20)),
                ("entry_date", models.DateField(default=django.utils.timezone.localdate)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12, validators=[django.core.validators.MinValueValidator(Decimal("0.01"))])),
                ("title", models.CharField(blank=True, max_length=140)),
                ("medicine_type", models.CharField(blank=True, choices=[("medicine", "Medicine"), ("vaccine", "Vaccine"), ("multivitamin", "Multivitamin")], max_length=20)),
                ("expense_category", models.CharField(blank=True, choices=[("diesel", "Diesel"), ("labor", "Labor"), ("electricity", "Electricity"), ("transport", "Transport"), ("maintenance", "Maintenance"), ("rent", "Farm Rent"), ("internet", "Internet"), ("service", "Service Charges"), ("misc", "Misc")], max_length=20)),
                ("allocation_scope", models.CharField(choices=[("individual", "One Goat"), ("selected", "Selected Goats"), ("all_active", "All Active Goats")], default="all_active", max_length=20)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_goat_cost_entries", to=settings.AUTH_USER_MODEL)),
                ("shed", models.ForeignKey(limit_choices_to={"shed_type": "goat"}, on_delete=django.db.models.deletion.PROTECT, related_name="goat_cost_entries", to="api.shed")),
            ],
            options={
                "verbose_name": "Goat Cost Entry",
                "verbose_name_plural": "Goat Cost Entries",
                "ordering": ["-entry_date", "-id"],
            },
        ),
        migrations.CreateModel(
            name="GoatSale",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sale_date", models.DateField(default=django.utils.timezone.localdate)),
                ("buyer_name", models.CharField(blank=True, max_length=140)),
                ("sale_weight_kg", models.DecimalField(decimal_places=2, max_digits=8, validators=[django.core.validators.MinValueValidator(Decimal("0.01"))])),
                ("rate_per_kg", models.DecimalField(decimal_places=2, max_digits=12, validators=[django.core.validators.MinValueValidator(Decimal("0.01"))])),
                ("gross_amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("discount_amount", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=12, validators=[django.core.validators.MinValueValidator(Decimal("0.00"))])),
                ("total_amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("locked_total_cost", models.DecimalField(decimal_places=2, max_digits=14)),
                ("payment_method", models.CharField(choices=[("cash", "Cash"), ("bank_transfer", "Bank Transfer"), ("cheque", "Cheque"), ("credit", "Credit"), ("other", "Other")], default="cash", max_length=20)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("goat", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="sale_record", to="api.goat")),
                ("recorded_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="recorded_goat_sales", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Goat Sale",
                "verbose_name_plural": "Goat Sales",
                "ordering": ["-sale_date", "-id"],
            },
        ),
        migrations.CreateModel(
            name="GoatAccountPayment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("payment_date", models.DateField(default=django.utils.timezone.localdate)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12, validators=[django.core.validators.MinValueValidator(Decimal("0.01"))])),
                ("payment_method", models.CharField(choices=[("cash", "Cash"), ("bank_transfer", "Bank Transfer"), ("cheque", "Cheque"), ("other", "Other")], default="bank_transfer", max_length=20)),
                ("reference", models.CharField(blank=True, max_length=120)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="goat_account_payments", to=settings.AUTH_USER_MODEL)),
                ("recorded_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="recorded_goat_account_payments", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Goat Account Payment",
                "verbose_name_plural": "Goat Account Payments",
                "ordering": ["-payment_date", "-id"],
            },
        ),
        migrations.CreateModel(
            name="GoatCostAllocation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12, validators=[django.core.validators.MinValueValidator(Decimal("0.00"))])),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("cost_entry", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="allocations", to="api.goatcostentry")),
                ("goat", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="cost_allocations", to="api.goat")),
                ("owner_snapshot", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="goat_cost_allocations", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["cost_entry__entry_date", "id"],
            },
        ),
        migrations.AddConstraint(
            model_name="goatcostallocation",
            constraint=models.UniqueConstraint(fields=("cost_entry", "goat"), name="unique_goat_cost_allocation"),
        ),
        migrations.RunPython(
            create_goat_shed_and_assign_device,
            reverse_goat_shed_assignment,
        ),
    ]
