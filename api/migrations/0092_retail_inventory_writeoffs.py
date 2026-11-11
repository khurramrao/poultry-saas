"""Audited FIFO lot-level inventory losses (without duplicating a sale or cash expense)."""
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0091_manual_prebooking_delivery_rate"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="EggPOSInventoryWriteOff",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("submission_token", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("writeoff_date", models.DateField(default=django.utils.timezone.localdate)),
                ("quantity", models.PositiveIntegerField()),
                ("unit_cost", models.DecimalField(max_digits=14, decimal_places=6)),
                ("total_cost", models.DecimalField(max_digits=14, decimal_places=2)),
                ("classification", models.CharField(max_length=15, default="routine", choices=[
                    ("routine", "Routine wastage (COGS)"),
                    ("exceptional", "Exceptional loss (expense)"),
                ])),
                ("reason", models.CharField(max_length=20, default="breakage", choices=[
                    ("breakage", "Breakage / damaged packaging"),
                    ("spoilage", "Spoilage / quality failure"),
                    ("expired", "Expired stock"),
                    ("accident", "Unusual accident / loss"),
                    ("other", "Other stock loss"),
                ])),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("is_voided", models.BooleanField(default=False)),
                ("voided_at", models.DateTimeField(blank=True, null=True)),
                ("void_reason", models.CharField(blank=True, max_length=500)),
                ("lot", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="writeoffs", to="api.eggposinventorylot")),
                ("recorded_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="retail_stock_writeoffs", to=settings.AUTH_USER_MODEL)),
                ("voided_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="retail_stock_writeoffs_voided", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
    ]
