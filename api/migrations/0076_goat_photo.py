from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("api", "0075_egg_pos_funds_transfer")]

    operations = [
        migrations.AddField(
            model_name="goat",
            name="photo_data",
            field=models.BinaryField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="goat",
            name="photo_mime",
            field=models.CharField(blank=True, default="", max_length=24),
        ),
    ]
