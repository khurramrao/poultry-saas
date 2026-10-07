from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [("api", "0066_egg_pos_sale_reversal")]
    operations = [
        migrations.AddField(model_name="eggposfarmtransfer", name="is_voided", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="eggposfarmtransfer", name="void_reason", field=models.CharField(blank=True, max_length=255)),
        migrations.AddField(model_name="eggposfarmtransfer", name="voided_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="eggposfarmtransfer", name="voided_by", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="egg_pos_farm_transfers_voided", to=settings.AUTH_USER_MODEL)),
    ]
