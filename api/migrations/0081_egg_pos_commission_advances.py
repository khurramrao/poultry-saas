# Generated for RayNoor commission advances; separate from legacy paid-period records.
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0080_online_offer_seo"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="EggPOSCommissionAdvancePayment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("payment_date", models.DateField(default=django.utils.timezone.localdate)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("payment_method", models.CharField(choices=[("cash", "Cash"), ("bank_transfer", "Bank Transfer"), ("easypaisa", "Easypaisa"), ("jazzcash", "JazzCash"), ("other", "Other")], default="bank_transfer", max_length=30)),
                ("reference", models.CharField(blank=True, max_length=120)),
                ("notes", models.TextField(blank=True)),
                ("request_token", models.CharField(max_length=36, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("recorded_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="egg_pos_commission_advances_recorded", to=settings.AUTH_USER_MODEL)),
                ("salesperson", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="egg_pos_commission_advance_payments", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["payment_date", "id"]},
        ),
        migrations.CreateModel(
            name="EggPOSCommissionAdvanceAllocation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("applied_date", models.DateField(default=django.utils.timezone.localdate)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("commission", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="advance_allocations", to="api.eggposcommissionperiod")),
                ("payment", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="allocations", to="api.eggposcommissionadvancepayment")),
            ],
            options={"ordering": ["applied_date", "id"]},
        ),
        migrations.AddConstraint(
            model_name="eggposcommissionadvanceallocation",
            constraint=models.UniqueConstraint(fields=("payment", "commission"), name="uniq_egg_pos_advance_alloc_per_period"),
        ),
    ]
