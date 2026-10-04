from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0063_egg_pos_sale_units"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="EggPOSOwnerCapitalTransaction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("transaction_date", models.DateField(default=django.utils.timezone.localdate)),
                ("transaction_type", models.CharField(choices=[("opening", "Opening Owner Capital"), ("additional", "Additional Owner Investment"), ("withdrawal", "Owner Withdrawal")], default="opening", max_length=20)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=16)),
                ("cash_account", models.CharField(choices=[("cash", "Main Cash"), ("bank", "Bank / Digital")], default="cash", max_length=20)),
                ("reference", models.CharField(blank=True, max_length=120)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("recorded_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="egg_pos_owner_capital_transactions_recorded", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-transaction_date", "-id"]},
        ),
    ]
