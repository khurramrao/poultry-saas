"""Marketing attribution only; no changes to POS accounting or inventory."""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone

CHANNELS = [
    ("tiktok", "TikTok"), ("facebook", "Facebook"), ("instagram", "Instagram"),
    ("whatsapp", "WhatsApp"), ("website", "Website"), ("phone", "Phone Call"),
    ("direct", "Direct / Walk-in"), ("other", "Other"),
]


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0086_egg_pos_manual_prebookings"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="EggPOSMarketingCustomer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=150)),
                ("name_key", models.CharField(max_length=150)),
                ("phone_key", models.CharField(max_length=20)),
                ("acquired_from", models.CharField(choices=CHANNELS, max_length=20)),
                ("acquired_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("pos_customer", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="marketing_profiles", to="api.eggposcustomer")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="egg_pos_marketing_profiles_created", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.CreateModel(
            name="EggPOSMarketingSale",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("order_source", models.CharField(choices=CHANNELS, default="direct", max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("marketing_customer", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="attributed_sales", to="api.eggposmarketingcustomer")),
                ("recorded_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="egg_pos_marketing_sale_sources", to=settings.AUTH_USER_MODEL)),
                ("sale", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="marketing_attribution", to="api.eggpossale")),
            ],
        ),
        migrations.AddConstraint(
            model_name="eggposmarketingcustomer",
            constraint=models.UniqueConstraint(fields=("phone_key", "name_key"), name="ep_marketing_phone_name_uq"),
        ),
    ]
