from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0049_relaychannel_automation"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="LayerHenCountHistory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("effective_date", models.DateField(default=django.utils.timezone.localdate)),
                ("active_hens", models.PositiveIntegerField()),
                ("notes", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("batch", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="active_hen_history", to="api.batch")),
                ("recorded_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="recorded_layer_hen_counts", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Layer Active Hen Count",
                "verbose_name_plural": "Layer Active Hen Counts",
                "ordering": ["-effective_date", "-id"],
            },
        ),
        migrations.AddConstraint(
            model_name="layerhencounthistory",
            constraint=models.UniqueConstraint(fields=("batch", "effective_date"), name="unique_layer_hen_count_per_batch_date"),
        ),
    ]
