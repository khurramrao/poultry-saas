from decimal import Decimal

import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0042_eggproductionentry_eggsale"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Goat",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("goat_code", models.CharField(blank=True, editable=False, max_length=20, unique=True)),
                ("tag_number", models.CharField(blank=True, help_text="Ear tag / physical identification number.", max_length=50, null=True, unique=True)),
                ("name", models.CharField(blank=True, max_length=100)),
                ("breed", models.CharField(blank=True, max_length=100)),
                ("sex", models.CharField(choices=[("male", "Male"), ("female", "Female")], max_length=10)),
                ("shed_label", models.CharField(default="Shed 3", help_text="Kept separate from poultry Shed records for now.", max_length=80)),
                ("acquisition_type", models.CharField(choices=[("purchased", "Purchased"), ("born_on_farm", "Born on Farm")], default="purchased", max_length=20)),
                ("purchase_date", models.DateField(default=django.utils.timezone.localdate)),
                ("date_of_birth", models.DateField(blank=True, null=True)),
                ("purchase_cost", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=12, validators=[django.core.validators.MinValueValidator(Decimal("0.00"))])),
                ("purchase_weight_kg", models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True, validators=[django.core.validators.MinValueValidator(Decimal("0.01"))])),
                ("status", models.CharField(choices=[("active", "Active"), ("sold", "Sold"), ("deceased", "Deceased")], default="active", max_length=20)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_goats", to=settings.AUTH_USER_MODEL)),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="owned_goats", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Goat",
                "verbose_name_plural": "Goats",
                "ordering": ["goat_code", "id"],
            },
        ),
        migrations.CreateModel(
            name="GoatWeightRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("record_date", models.DateField(default=django.utils.timezone.localdate)),
                ("weight_kg", models.DecimalField(decimal_places=2, max_digits=8, validators=[django.core.validators.MinValueValidator(Decimal("0.01"))])),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("goat", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="weight_records", to="api.goat")),
                ("recorded_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="recorded_goat_weights", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Goat Weight Record",
                "verbose_name_plural": "Goat Weight Records",
                "ordering": ["-record_date", "-id"],
            },
        ),
    ]
