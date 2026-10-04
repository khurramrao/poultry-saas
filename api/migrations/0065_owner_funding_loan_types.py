from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0064_egg_pos_owner_capital"),
    ]

    operations = [
        migrations.AlterField(
            model_name="eggposownercapitaltransaction",
            name="transaction_type",
            field=models.CharField(
                choices=[
                    ("opening", "Opening Owner Capital"),
                    ("additional", "Additional Owner Investment"),
                    ("withdrawal", "Owner Withdrawal / Drawings"),
                    ("owner_loan", "Owner Loan to Business"),
                    ("loan_repayment", "Repay Owner Loan"),
                ],
                default="opening",
                max_length=20,
            ),
        ),
    ]
