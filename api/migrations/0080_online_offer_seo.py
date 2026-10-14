"""Add optional per-pack SEO title and description. Keeps existing records intact."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("api", "0079_merge_goat_photo_and_online_gallery")]

    operations = [
        migrations.AddField(model_name="onlineoffer", name="seo_title", field=models.CharField(max_length=70, blank=True, default="", help_text="Optional browser/search result title, max 70 characters.")),
        migrations.AddField(model_name="onlineoffer", name="seo_description", field=models.CharField(max_length=160, blank=True, default="", help_text="Optional search result summary, max 160 characters.")),
    ]
