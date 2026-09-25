from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0050_layer_hen_count_history"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="FarmAccountPayment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("payment_date", models.DateField(default=django.utils.timezone.localdate)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("payment_method", models.CharField(choices=[("cash", "Cash"), ("bank_transfer", "Bank Transfer"), ("cheque", "Cheque"), ("other", "Other")], default="bank_transfer", max_length=20)),
                ("reference", models.CharField(blank=True, max_length=120)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("batch", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="farm_account_payments", to="api.batch")),
                ("recorded_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="recorded_farm_account_payments", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Farm Contribution Payment",
                "verbose_name_plural": "Farm Contribution Payments",
                "ordering": ["-payment_date", "-id"],
            },
        ),
        migrations.CreateModel(
            name="FarmSaleWithdrawal",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("withdrawal_date", models.DateField(default=django.utils.timezone.localdate)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("payment_method", models.CharField(choices=[("cash", "Cash"), ("bank_transfer", "Bank Transfer"), ("cheque", "Cheque"), ("other", "Other")], default="bank_transfer", max_length=20)),
                ("reference", models.CharField(blank=True, max_length=120)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("batch", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="farm_sale_withdrawals", to="api.batch")),
                ("recorded_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="recorded_farm_sale_withdrawals", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Farm Sale Withdrawal",
                "verbose_name_plural": "Farm Sale Withdrawals",
                "ordering": ["-withdrawal_date", "-id"],
            },
        ),
    ]
