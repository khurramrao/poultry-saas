"""New forward-only commission loss balances; legacy periods keep zero defaults."""
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0081_egg_pos_commission_advances"),
    ]

    operations = [
        migrations.AddField(
            model_name="eggposcommissionperiod",
            name="loss_carry_in",
            field=models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0.00")),
        ),
        migrations.AddField(
            model_name="eggposcommissionperiod",
            name="loss_carry_out",
            field=models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0.00")),
        ),
    ]
