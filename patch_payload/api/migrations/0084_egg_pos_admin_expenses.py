import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0083_egg_pos_purchase_void"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="EggPOSAdminExpense",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("expense_date", models.DateField(default=django.utils.timezone.localdate)),
                ("category", models.CharField(choices=[
                    ("rent", "Office / Shop Rent"), ("utilities", "Electricity / Utilities"),
                    ("internet", "Internet / Phone"), ("stationery", "Stationery / Printing"),
                    ("supplies", "Office Consumables"), ("repair", "Repairs & Maintenance"),
                    ("marketing", "Marketing / Advertising"), ("professional", "Professional / Admin Fees"),
                    ("equipment", "Equipment (Fixed Asset)"), ("other", "Other General Expense"),
                ], max_length=24)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("payment_source", models.CharField(choices=[("cash", "Main Cash"), ("bank", "Bank / Digital Wallet")], max_length=10)),
                ("paid_to", models.CharField(blank=True, max_length=160)),
                ("reference", models.CharField(blank=True, max_length=120)),
                ("notes", models.TextField(blank=True)),
                ("request_token", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("is_voided", models.BooleanField(default=False)),
                ("voided_at", models.DateTimeField(blank=True, null=True)),
                ("void_reason", models.CharField(blank=True, max_length=500)),
                ("recorded_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="egg_pos_admin_expenses", to=settings.AUTH_USER_MODEL)),
                ("voided_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="egg_pos_admin_expenses_voided", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-expense_date", "-id"],
                "indexes": [models.Index(fields=["expense_date", "is_voided"], name="ep_adminexp_date_idx")],
            },
        ),
    ]
