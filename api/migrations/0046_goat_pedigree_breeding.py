import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0045_goat_operations_finance_and_shed"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="goat",
            name="sire",
            field=models.ForeignKey(
                blank=True,
                limit_choices_to={"sex": "male"},
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="sired_offspring",
                to="api.goat",
            ),
        ),
        migrations.AddField(
            model_name="goat",
            name="dam",
            field=models.ForeignKey(
                blank=True,
                limit_choices_to={"sex": "female"},
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="dam_offspring",
                to="api.goat",
            ),
        ),
        migrations.AddField(
            model_name="goat",
            name="sire_external",
            field=models.CharField(
                blank=True,
                help_text="Known sire identity when the sire is not registered in RayNoor.",
                max_length=120,
            ),
        ),
        migrations.AddField(
            model_name="goat",
            name="dam_external",
            field=models.CharField(
                blank=True,
                help_text="Known dam identity when the dam is not registered in RayNoor.",
                max_length=120,
            ),
        ),
        migrations.CreateModel(
            name="GoatBreedingRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("mating_date", models.DateField(default=django.utils.timezone.localdate)),
                ("expected_kidding_date", models.DateField(blank=True, null=True)),
                ("status", models.CharField(choices=[("mated", "Mated"), ("pregnant", "Pregnant"), ("kidded", "Kidded"), ("failed", "Not Pregnant / Failed"), ("cancelled", "Cancelled")], default="mated", max_length=20)),
                ("relationship_risk", models.CharField(choices=[("safe", "Suitable"), ("warning", "Warning"), ("unknown", "Unknown")], default="unknown", max_length=20)),
                ("relationship_label", models.CharField(blank=True, max_length=180)),
                ("relationship_details", models.TextField(blank=True)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("buck", models.ForeignKey(limit_choices_to={"sex": "male"}, on_delete=django.db.models.deletion.PROTECT, related_name="breeding_records_as_buck", to="api.goat")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_goat_breeding_records", to=settings.AUTH_USER_MODEL)),
                ("doe", models.ForeignKey(limit_choices_to={"sex": "female"}, on_delete=django.db.models.deletion.PROTECT, related_name="breeding_records_as_doe", to="api.goat")),
            ],
            options={
                "verbose_name": "Goat Breeding Record",
                "verbose_name_plural": "Goat Breeding Records",
                "ordering": ["-mating_date", "-id"],
            },
        ),
        migrations.CreateModel(
            name="GoatKiddingRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kidding_date", models.DateField(default=django.utils.timezone.localdate)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("breeding_record", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="kidding_record", to="api.goatbreedingrecord")),
                ("kids", models.ManyToManyField(blank=True, related_name="kidding_events", to="api.goat")),
                ("recorded_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="recorded_goat_kiddings", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Goat Kidding Record",
                "verbose_name_plural": "Goat Kidding Records",
                "ordering": ["-kidding_date", "-id"],
            },
        ),
    ]
