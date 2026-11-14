from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('api', '0098_admin_variable_payments'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name='EggPOSSalaryAdvanceCorrection',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('old_amount', models.DecimalField(decimal_places=2, max_digits=14)),
                ('new_amount', models.DecimalField(decimal_places=2, max_digits=14)),
                ('reason', models.CharField(max_length=500)),
                ('corrected_at', models.DateTimeField(auto_now_add=True)),
                ('advance', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='corrections', to='api.eggpossalaryadvance')),
                ('corrected_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='egg_pos_salary_advance_corrections', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-corrected_at', '-id']},
        ),
    ]
