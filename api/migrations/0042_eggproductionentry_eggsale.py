from decimal import Decimal

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("api", "0041_investoraccountpayment"),
    ]

    operations = [
        migrations.CreateModel(
            name="EggProductionEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("production_date", models.DateField(default=django.utils.timezone.localdate)),
                ("eggs_collected", models.PositiveIntegerField(default=0)),
                ("damaged_eggs", models.PositiveIntegerField(default=0)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("batch", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="egg_production_entries", to="api.batch")),
                ("recorded_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="recorded_egg_production_entries", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Egg Production Entry",
                "verbose_name_plural": "Egg Production Entries",
                "ordering": ["-production_date", "-id"],
            },
        ),
        migrations.CreateModel(
            name="EggSale",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sale_date", models.DateField(default=django.utils.timezone.localdate)),
                ("buyer_name", models.CharField(blank=True, help_text="Optional buyer/customer name.", max_length=150)),
                ("eggs_sold", models.PositiveIntegerField()),
                ("rate_per_egg", models.DecimalField(decimal_places=2, max_digits=10)),
                ("discount_amount", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=12)),
                ("payment_method", models.CharField(choices=[("cash", "Cash"), ("bank_transfer", "Bank Transfer"), ("credit", "Credit"), ("other", "Other")], default="cash", max_length=20)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("batch", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="egg_sales", to="api.batch")),
                ("recorded_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="recorded_egg_sales", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Egg Sale",
                "verbose_name_plural": "Egg Sales",
                "ordering": ["-sale_date", "-id"],
            },
        ),
    ]
