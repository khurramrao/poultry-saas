"""Separate physical source SKUs, combined selling products, and manual online packing allocations."""
from django.db import migrations, models
import django.db.models.deletion
import django.core.validators

class Migration(migrations.Migration):
    dependencies = [('api', '0087_egg_pos_marketing_attribution')]
    operations = [
        migrations.AddField(
            model_name='eggposproduct', name='selling_product',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='source_skus', to='api.eggposproduct',
                help_text='Optional: combine this source SKU under another product for POS and online sales.'),
        ),
        migrations.CreateModel(
            name='OnlineOrderItemSource',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantity', models.PositiveIntegerField(validators=[django.core.validators.MinValueValidator(1)])),
                ('item', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='source_allocations', to='api.onlineorderitem')),
                ('source_product', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='api.eggposproduct')),
            ],
        ),
        migrations.AddConstraint(
            model_name='onlineorderitemsource',
            constraint=models.UniqueConstraint(fields=('item','source_product'), name='unique_online_item_source_sku'),
        ),
    ]
