from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0068_egg_pos_sale_audit"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(model_name="eggsale", name="is_voided", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="eggsale", name="void_reason", field=models.CharField(blank=True, max_length=255)),
        migrations.AddField(model_name="eggsale", name="voided_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="eggsale", name="voided_by", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="voided_direct_egg_sales", to=settings.AUTH_USER_MODEL)),
        migrations.CreateModel(
            name="EggStockWastage",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False, auto_created=True, verbose_name="ID")),
                ("damage_date", models.DateField(default=django.utils.timezone.localdate)),
                ("quantity", models.PositiveIntegerField()),
                ("reason", models.CharField(max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("batch", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="egg_stock_wastage", to="api.batch")),
                ("recorded_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="egg_wastage_recorded", to=settings.AUTH_USER_MODEL)),
                ("source_sale", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="wastage_reclassification", to="api.eggsale")),
            ],
            options={"ordering": ["-damage_date", "-id"]},
        ),
        migrations.CreateModel(
            name="EggSaleCorrectionAudit",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False, auto_created=True, verbose_name="ID")),
                ("action", models.CharField(choices=[("edit", "Edit"), ("reverse", "Reverse"), ("restore", "Undo reversal"), ("damage", "Convert to damage")], max_length=12)),
                ("reason", models.CharField(max_length=255)),
                ("before", models.JSONField(default=dict)),
                ("after", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("admin", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="direct_egg_sale_corrections", to=settings.AUTH_USER_MODEL)),
                ("sale", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="correction_audit", to="api.eggsale")),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
    ]
