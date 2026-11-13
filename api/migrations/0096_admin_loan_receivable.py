import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone

class Migration(migrations.Migration):
    dependencies = [
        ('api', '0095_prebooking_jar_units'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name='EggPOSAdminLoanTransaction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('advance', 'Paid to administrator'), ('repayment', 'Repaid by administrator')], max_length=12)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=14)),
                ('transaction_date', models.DateField(default=django.utils.timezone.localdate)),
                ('payment_source', models.CharField(choices=[('cash','Main Cash'), ('bank','Bank / Digital Wallet')], max_length=10)),
                ('reference', models.CharField(blank=True, max_length=120)),
                ('notes', models.CharField(blank=True, max_length=500)),
                ('request_token', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('administrator', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='raynoor_admin_loans_received', to=settings.AUTH_USER_MODEL)),
                ('recorded_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='raynoor_admin_loans_recorded', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-transaction_date', '-pk']},
        ),
        migrations.AddIndex(
            model_name='eggposadminloantransaction',
            index=models.Index(fields=['administrator', 'transaction_date'], name='ep_adminloan_date_idx'),
        ),
    ]
