import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ('api', '0084_egg_pos_admin_expenses'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='EggPOSSalaryPlan',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('effective_month', models.DateField()),
                ('monthly_salary', models.DecimalField(decimal_places=2, max_digits=14)),
                ('notes', models.CharField(blank=True, max_length=250)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='egg_pos_salary_plans', to=settings.AUTH_USER_MODEL)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='egg_pos_salary_plans_created', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['employee_id', '-effective_month', '-id']},
        ),
        migrations.CreateModel(
            name='EggPOSPayrollPeriod',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('month', models.DateField()),
                ('salary_amount', models.DecimalField(decimal_places=2, max_digits=14)),
                ('posted_at', models.DateTimeField(auto_now_add=True)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='egg_pos_payroll_periods', to=settings.AUTH_USER_MODEL)),
                ('posted_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='egg_pos_payroll_posted', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-month', '-id']},
        ),
        migrations.CreateModel(
            name='EggPOSSalaryAdvance',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('paid_on', models.DateField(default=django.utils.timezone.localdate)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=14)),
                ('payment_source', models.CharField(choices=[('cash', 'Main Cash'), ('bank', 'Bank / Digital Wallet')], max_length=10)),
                ('reference', models.CharField(blank=True, max_length=120)),
                ('notes', models.CharField(blank=True, max_length=250)),
                ('request_token', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='egg_pos_salary_advances', to=settings.AUTH_USER_MODEL)),
                ('recorded_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='egg_pos_salary_advances_recorded', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['paid_on', 'id']},
        ),
        migrations.CreateModel(
            name='EggPOSSalaryAdvanceAllocation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount', models.DecimalField(decimal_places=2, max_digits=14)),
                ('allocated_at', models.DateTimeField(auto_now_add=True)),
                ('advance', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='allocations', to='api.eggpossalaryadvance')),
                ('payroll', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='advance_allocations', to='api.eggpospayrollperiod')),
            ],
            options={'ordering': ['payroll__month', 'id']},
        ),
        migrations.CreateModel(
            name='EggPOSSalaryPayment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('paid_on', models.DateField(default=django.utils.timezone.localdate)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=14)),
                ('payment_source', models.CharField(choices=[('cash', 'Main Cash'), ('bank', 'Bank / Digital Wallet')], max_length=10)),
                ('reference', models.CharField(blank=True, max_length=120)),
                ('request_token', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('payroll', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='payments', to='api.eggpospayrollperiod')),
                ('recorded_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='egg_pos_salary_payments_recorded', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-paid_on', '-id']},
        ),
        migrations.AddConstraint(model_name='eggpossalaryplan', constraint=models.UniqueConstraint(fields=('employee','effective_month'), name='ep_salaryplan_employee_month_uniq')),
        migrations.AddConstraint(model_name='eggpospayrollperiod', constraint=models.UniqueConstraint(fields=('employee','month'), name='ep_payroll_employee_month_uniq')),
        migrations.AddConstraint(model_name='eggpossalaryadvanceallocation', constraint=models.UniqueConstraint(fields=('advance','payroll'), name='ep_salaryadvance_payroll_uniq')),
    ]
