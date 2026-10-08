from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0067_farm_transfer_edit_void"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name="EggPOSSaleAudit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action", models.CharField(max_length=12, choices=[("edit", "Edit"), ("reverse", "Reverse"), ("restore", "Restore")])),
                ("reason", models.CharField(max_length=255)),
                ("before", models.JSONField(default=dict)),
                ("after", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("admin", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="egg_pos_invoice_corrections", to=settings.AUTH_USER_MODEL)),
                ("sale", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="correction_history", to="api.eggpossale")),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
    ]
