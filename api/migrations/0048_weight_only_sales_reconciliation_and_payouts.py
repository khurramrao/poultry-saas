from decimal import Decimal

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0047_goat_estimated_birth_date"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="salerecord",
            name="sale_mode",
            field=models.CharField(
                choices=[
                    ("counted", "Counted Birds"),
                    ("weight_only", "Weight Only / Bird Count Unknown"),
                ],
                default="counted",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="salerecord",
            name="cogs_locked",
            field=models.BooleanField(default=True),
        ),
        migrations.AlterField(
            model_name="salerecord",
            name="birds_sold",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="BatchBirdSaleReconciliation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reconciliation_date", models.DateField(default=django.utils.timezone.localdate)),
                ("counted_birds_sold", models.PositiveIntegerField(default=0)),
                ("reconciled_weight_only_birds", models.PositiveIntegerField(default=0)),
                ("total_birds_sold", models.PositiveIntegerField(default=0)),
                ("total_weight_sold_kg", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=14)),
                ("total_sales_revenue", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=14)),
                ("total_cogs_snapshot", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=14)),
                ("remaining_cogs_realized", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=14)),
                ("notes", models.TextField(blank=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("reversed_at", models.DateTimeField(blank=True, null=True)),
                ("batch", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="bird_sale_reconciliation", to="api.batch")),
                ("confirmed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="confirmed_all_birds_sold_reconciliations", to=settings.AUTH_USER_MODEL)),
                ("reversed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reversed_all_birds_sold_reconciliations", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Batch Bird Sale Reconciliation",
                "verbose_name_plural": "Batch Bird Sale Reconciliations",
            },
        ),
        migrations.CreateModel(
            name="InvestorSalePayout",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("payout_date", models.DateField(default=django.utils.timezone.localdate)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("payment_method", models.CharField(choices=[("cash", "Cash"), ("bank_transfer", "Bank Transfer"), ("cheque", "Cheque"), ("other", "Other")], default="bank_transfer", max_length=20)),
                ("reference", models.CharField(blank=True, max_length=120)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("allocation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="sale_payouts", to="api.investorallocation")),
                ("recorded_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="recorded_investor_sale_payouts", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Investor Sale Payout",
                "verbose_name_plural": "Investor Sale Payouts",
                "ordering": ["-payout_date", "-id"],
            },
        ),
    ]
