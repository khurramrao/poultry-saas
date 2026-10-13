from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ('api', '0074_online_order_event_audit'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='EggPOSFundsTransfer',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('transfer_date', models.DateField(default=django.utils.timezone.localdate)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('direction', models.CharField(choices=[('cash_to_bank', 'Main Cash → Bank / Digital'), ('bank_to_cash', 'Bank / Digital → Main Cash')], max_length=20)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=14)),
                ('reference', models.CharField(blank=True, max_length=120)),
                ('notes', models.CharField(blank=True, max_length=500)),
                ('request_token', models.UUIDField(editable=False, unique=True)),
                ('is_voided', models.BooleanField(default=False)),
                ('void_reason', models.CharField(blank=True, max_length=500)),
                ('voided_at', models.DateTimeField(blank=True, null=True)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='egg_pos_funds_transfers', to=settings.AUTH_USER_MODEL)),
                ('voided_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='egg_pos_funds_transfers_voided', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at', '-id']},
        ),
    ]
