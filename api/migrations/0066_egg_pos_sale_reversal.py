from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('api', '0065_owner_funding_loan_types'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(model_name='eggpossale', name='is_reversed', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='eggpossale', name='reversal_reason', field=models.CharField(blank=True, max_length=255)),
        migrations.AddField(model_name='eggpossale', name='reversed_at', field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(
            model_name='eggpossale', name='reversed_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name='egg_pos_sales_reversed', to=settings.AUTH_USER_MODEL),
        ),
    ]
