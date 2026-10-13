from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('api', '0077_online_offer_photo_display_and_alt')]

    operations = [
        migrations.AddField(
            model_name='onlineoffer',
            name='description',
            field=models.TextField(blank=True, default='', help_text='Customer-facing pack details. Plain text; line breaks are supported.'),
        ),
        migrations.AddField(
            model_name='onlineoffer',
            name='egg_weight_label',
            field=models.CharField(blank=True, default='', max_length=80, help_text='Optional actual egg weight, e.g. 55–60 g per egg.'),
        ),
        migrations.CreateModel(
            name='OnlineOfferGalleryPhoto',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('photo_data', models.BinaryField(editable=False)),
                ('photo_mime', models.CharField(default='image/jpeg', editable=False, max_length=30)),
                ('alt_text', models.CharField(blank=True, max_length=180)),
                ('sort_order', models.PositiveSmallIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('offer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='gallery_photos', to='api.onlineoffer')),
            ],
            options={'ordering': ('sort_order', 'id')},
        ),
    ]
