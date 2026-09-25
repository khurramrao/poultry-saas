from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0051_farm_account_payment_and_sale_withdrawal"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="GoatSalePayout",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("payout_date", models.DateField(default=django.utils.timezone.localdate)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("payment_method", models.CharField(choices=[("cash", "Cash"), ("bank_transfer", "Bank Transfer"), ("cheque", "Cheque"), ("other", "Other")], default="bank_transfer", max_length=20)),
                ("reference", models.CharField(blank=True, max_length=120)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="goat_sale_payouts", to=settings.AUTH_USER_MODEL)),
                ("recorded_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="recorded_goat_sale_payouts", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Goat Sale Payout / Withdrawal",
                "verbose_name_plural": "Goat Sale Payouts / Withdrawals",
                "ordering": ["-payout_date", "-id"],
            },
        ),
    ]
