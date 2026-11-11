"""Permit sealed jars as manual prebooking units for ghee and honey."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("api", "0094_supplier_payment_method_audit")]

    operations = [
        migrations.AlterField(
            model_name="eggposmanualprebookingline",
            name="unit",
            field=models.CharField(
                max_length=12,
                default="tray",
                choices=[
                    ("egg", "Eggs"), ("pack6", "6-Egg Packs"),
                    ("dozen", "Dozens (12)"), ("tray", "Trays (30)"),
                    ("jar", "Sealed Jars"),
                ],
            ),
        ),
    ]
