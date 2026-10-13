from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('api', '0076_online_offer_photo')]

    operations = [
        migrations.AddField(
            model_name='onlineoffer', name='photo_fit',
            field=models.CharField(choices=[('contain', 'Show full photo'), ('cover', 'Fill frame (crop edges)')], default='contain', max_length=8),
        ),
        migrations.AddField(
            model_name='onlineoffer', name='photo_position',
            field=models.CharField(choices=[('center', 'Center'), ('top', 'Top'), ('bottom', 'Bottom'), ('left', 'Left'), ('right', 'Right')], default='center', max_length=6),
        ),
        migrations.AddField(
            model_name='onlineoffer', name='photo_alt',
            field=models.CharField(blank=True, default='', help_text='Descriptive image alt text for accessibility and search engines; plain text only.', max_length=180),
        ),
    ]
