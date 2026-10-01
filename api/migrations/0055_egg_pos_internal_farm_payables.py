from decimal import Decimal
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


def backfill_transfer_numbers(apps, schema_editor):
    Transfer = apps.get_model("api", "EggPOSFarmTransfer")
    Sale = apps.get_model("api", "EggPOSSale")

    for transfer in Transfer.objects.all().order_by("id").iterator():
        if not transfer.transfer_number:
            transfer.transfer_number = f"RNET-{transfer.id:05d}"
            transfer.save(update_fields=["transfer_number"])

    # Keep Egg POS invoice numbering in the requested short format.
    for sale in Sale.objects.all().order_by("id").iterator():
        wanted = f"RNE-{sale.id:05d}"
        if sale.sale_number != wanted:
            sale.sale_number = wanted
            sale.save(update_fields=["sale_number"])


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0054_egg_pos_credit_invoice"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="eggposfarmtransfer",
            name="transfer_number",
            field=models.CharField(blank=True, max_length=30, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="eggposfarmtransfer",
            name="payment_due_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="EggPOSFarmTransferPayment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("payment_date", models.DateField(default=django.utils.timezone.localdate)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("payment_method", models.CharField(choices=[("cash", "Cash"), ("bank_transfer", "Bank Transfer"), ("easypaisa", "Easypaisa"), ("jazzcash", "JazzCash"), ("other", "Other")], default="bank_transfer", max_length=30)),
                ("reference", models.CharField(blank=True, max_length=120)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("recorded_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="egg_pos_farm_transfer_payments_recorded", to=settings.AUTH_USER_MODEL)),
                ("transfer", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="payments", to="api.eggposfarmtransfer")),
            ],
            options={"ordering": ["payment_date", "id"]},
        ),
        migrations.RunPython(backfill_transfer_numbers, migrations.RunPython.noop),
    ]
