import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0061_backfill_egg_pos_journals"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="EggPOSCashHandoverRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("handover_date", models.DateField(default=django.utils.timezone.localdate)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("destination", models.CharField(choices=[("cash", "Main Cash"), ("bank_transfer", "Bank")], default="cash", max_length=30)),
                ("reference", models.CharField(blank=True, max_length=120)),
                ("notes", models.TextField(blank=True)),
                ("status", models.CharField(choices=[("pending", "Pending Confirmation"), ("confirmed", "Confirmed"), ("rejected", "Rejected")], default="pending", max_length=20)),
                ("submitted_at", models.DateTimeField(auto_now_add=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("rejection_reason", models.CharField(blank=True, max_length=255)),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="egg_pos_cash_handover_requests_reviewed", to=settings.AUTH_USER_MODEL)),
                ("salesperson", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="egg_pos_cash_handover_requests", to=settings.AUTH_USER_MODEL)),
                ("settlement", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="handover_request", to="api.eggposcashsettlement")),
            ],
            options={"ordering": ["-handover_date", "-id"]},
        ),
        migrations.AddIndex(
            model_name="eggposcashhandoverrequest",
            index=models.Index(fields=["salesperson", "status", "handover_date"], name="api_cashreq_user_idx"),
        ),
    ]
