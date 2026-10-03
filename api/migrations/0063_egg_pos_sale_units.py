from decimal import Decimal

from django.db import migrations, models


def backfill_sale_units(apps, schema_editor):
    SaleItem = apps.get_model("api", "EggPOSSaleItem")
    for item in SaleItem.objects.all().iterator():
        qty = int(item.quantity or 1)
        rate = Decimal(item.unit_price or 0)
        item.sale_unit = "egg"
        item.unit_count = qty
        item.unit_size = 1
        item.unit_rate = rate
        item.save(update_fields=["sale_unit", "unit_count", "unit_size", "unit_rate"])


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0062_egg_pos_cash_handover_requests"),
    ]

    operations = [
        migrations.AddField(
            model_name="eggpossaleitem",
            name="sale_unit",
            field=models.CharField(
                choices=[
                    ("egg", "Loose Egg"),
                    ("dozen", "Dozen (12 eggs)"),
                    ("tray", "Tray (30 eggs)"),
                    ("crate", "Paiti / Crate (360 eggs)"),
                ],
                default="egg",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="eggpossaleitem",
            name="unit_count",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="eggpossaleitem",
            name="unit_size",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="eggpossaleitem",
            name="unit_rate",
            field=models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=12),
        ),
        migrations.RunPython(backfill_sale_units, migrations.RunPython.noop),
    ]
