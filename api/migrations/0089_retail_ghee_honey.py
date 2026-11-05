from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('api', '0088_mixed_source_egg_stock')]
    operations = [
        migrations.AddField(
            model_name='eggposproduct', name='category',
            field=models.CharField(max_length=12, default='egg', db_index=True,
                choices=[('egg', 'Eggs'), ('ghee', 'Desi Ghee'), ('honey', 'Honey')]),
        ),
        migrations.AddField(
            model_name='eggpospurchaseitem', name='sticker_cost_total',
            field=models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00')),
        ),
        migrations.AlterField(
            model_name='onlineoffer', name='pack_size',
            field=models.PositiveSmallIntegerField(choices=[(1, '1 jar'), (6, '6 eggs'), (12, '12 eggs'), (30, '30 eggs')]),
        ),
        migrations.AlterField(
            model_name='eggpossaleitem', name='sale_unit',
            field=models.CharField(max_length=20, default='egg', choices=[
                ('egg', 'Loose Egg'), ('jar', 'Sealed Jar'), ('dozen', 'Dozen (12 eggs)'),
                ('tray', 'Tray (30 eggs)'), ('crate', 'Paiti / Crate (360 eggs)')]),
        ),
    ]
