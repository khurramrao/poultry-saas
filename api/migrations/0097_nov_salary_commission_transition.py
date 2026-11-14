from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('api', '0096_admin_loan_receivable')]
    operations = [
        migrations.AddField(model_name='eggpossalaryplan', name='commission_credit_amount',
                            field=models.DecimalField(decimal_places=2, max_digits=14, default=Decimal('0.00'))),
        migrations.AddField(model_name='eggpossalaryplan', name='commission_credit_reason',
                            field=models.CharField(max_length=500, blank=True)),
        migrations.AddField(model_name='eggpossalaryplan', name='commission_overlap_ids',
                            field=models.JSONField(default=list, blank=True)),
        migrations.AddField(model_name='eggpospayrollperiod', name='commission_credit_amount',
                            field=models.DecimalField(decimal_places=2, max_digits=14, default=Decimal('0.00'))),
    ]
