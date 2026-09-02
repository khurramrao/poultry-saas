from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("api", "0040_relaychannel"),
    ]

    operations = [
        migrations.CreateModel(
            name="InvestorAccountPayment",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "payment_date",
                    models.DateField(default=django.utils.timezone.localdate),
                ),
                (
                    "amount",
                    models.DecimalField(decimal_places=2, max_digits=12),
                ),
                (
                    "payment_method",
                    models.CharField(
                        choices=[
                            ("cash", "Cash"),
                            ("bank_transfer", "Bank Transfer"),
                            ("cheque", "Cheque"),
                            ("other", "Other"),
                        ],
                        default="bank_transfer",
                        max_length=20,
                    ),
                ),
                (
                    "reference",
                    models.CharField(blank=True, max_length=120),
                ),
                (
                    "notes",
                    models.TextField(blank=True),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True),
                ),
                (
                    "allocation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="account_payments",
                        to="api.investorallocation",
                    ),
                ),
                (
                    "recorded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="recorded_investor_account_payments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Investor Account Payment",
                "verbose_name_plural": "Investor Account Payments",
                "ordering": ["-payment_date", "-id"],
            },
        ),
    ]
