from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('api', '0075_egg_pos_funds_transfer')]

    operations = [
        migrations.AddField(
            model_name='onlineoffer', name='photo_data',
            field=models.BinaryField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name='onlineoffer', name='photo_mime',
            field=models.CharField(blank=True, default='', editable=False, max_length=30),
        ),
        migrations.AddField(
            model_name='onlineoffer', name='photo_updated_at',
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
    ]
