from decimal import Decimal

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0099_salary_advance_correction"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="EggPOSSkuTransfer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("quantity", models.PositiveIntegerField()),
                ("unit_cost_at_transfer", models.DecimalField(decimal_places=6, max_digits=14)),
                ("reason", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("is_reversed", models.BooleanField(default=False)),
                ("reversed_at", models.DateTimeField(blank=True, null=True)),
                ("reversal_reason", models.TextField(blank=True)),
                ("created_by", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="egg_pos_sku_transfers_created", to=settings.AUTH_USER_MODEL,
                )),
                ("reversed_by", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                    related_name="egg_pos_sku_transfers_reversed", to=settings.AUTH_USER_MODEL,
                )),
                ("source_lot", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="sku_transfers_out", to="api.eggposinventorylot",
                )),
                ("destination_lot", models.OneToOneField(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="sku_transfer_in", to="api.eggposinventorylot",
                )),
                ("source_product", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="sku_transfers_out", to="api.eggposproduct",
                )),
                ("destination_product", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="sku_transfers_in", to="api.eggposproduct",
                )),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
    ]
