import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [('api', '0097_nov_salary_commission_transition'),
                    migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(
            name='EggPOSAdminVariablePayment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('paid_on', models.DateField(default=django.utils.timezone.localdate)),
                ('amount', models.DecimalField(max_digits=14, decimal_places=2)),
                ('kind', models.CharField(max_length=12, choices=[('salary', 'Variable remuneration / wages (expense)'), ('drawing', 'Owner drawing (equity, not salary expense)')])),
                ('payment_source', models.CharField(max_length=10, choices=[('cash', 'Main Cash'), ('bank', 'Bank / Digital Wallet')])),
                ('reference', models.CharField(max_length=120, blank=True)),
                ('notes', models.CharField(max_length=500, blank=True)),
                ('request_token', models.UUIDField(default=uuid.uuid4, unique=True, editable=False)),
                ('recorded_at', models.DateTimeField(auto_now_add=True)),
                ('recipient', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='egg_pos_admin_variable_payments', to=settings.AUTH_USER_MODEL)),
                ('recorded_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='egg_pos_admin_variable_payments_recorded', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-paid_on', '-id']},
        ),
    ]
