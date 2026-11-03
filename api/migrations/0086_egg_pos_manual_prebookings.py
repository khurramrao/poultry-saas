# Manually maintained RayNoor social-media pre-bookings. Independent from orders/inventory/accounts.
import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ('api', '0085_egg_pos_staff_payroll'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='EggPOSManualPrebooking',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('customer_name', models.CharField(max_length=150)),
                ('customer_phone', models.CharField(max_length=40)),
                ('channel', models.CharField(choices=[('tiktok', 'TikTok'), ('facebook', 'Facebook'), ('instagram', 'Instagram'), ('whatsapp', 'WhatsApp'), ('phone', 'Phone Call'), ('other', 'Other')], max_length=20)),
                ('delivery_area', models.CharField(blank=True, max_length=180)),
                ('expected_date', models.DateField(blank=True, null=True)),
                ('notes', models.TextField(blank=True)),
                ('status', models.CharField(choices=[('awaiting_stock', 'Awaiting Stock'), ('contact_customer', 'Stock Arrived — Reconfirm'), ('confirmed', 'Customer Reconfirmed'), ('completed', 'Completed'), ('cancelled', 'Cancelled / Declined')], db_index=True, default='awaiting_stock', max_length=24)),
                ('stock_arrived_at', models.DateTimeField(blank=True, null=True)),
                ('reconfirmed_at', models.DateTimeField(blank=True, null=True)),
                ('reconfirmation_channel', models.CharField(blank=True, choices=[('tiktok', 'TikTok'), ('facebook', 'Facebook'), ('instagram', 'Instagram'), ('whatsapp', 'WhatsApp'), ('phone', 'Phone Call'), ('other', 'Other')], max_length=20)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('invoice_reference', models.CharField(blank=True, help_text='Manual POS invoice/reference only; no automatic sale link', max_length=80)),
                ('cancelled_at', models.DateTimeField(blank=True, null=True)),
                ('cancellation_reason', models.CharField(blank=True, max_length=255)),
                ('request_token', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='egg_manual_prebookings_created', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ('-created_at', '-id')},
        ),
        migrations.CreateModel(
            name='EggPOSManualPrebookingLine',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantity', models.PositiveIntegerField()),
                ('unit', models.CharField(choices=[('egg', 'Eggs'), ('pack6', '6-Egg Packs'), ('dozen', 'Dozens (12)'), ('tray', 'Trays (30)')], default='tray', max_length=12)),
                ('booking', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lines', to='api.eggposmanualprebooking')),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='manual_prebooking_lines', to='api.eggposproduct')),
            ],
            options={'ordering': ('id',)},
        ),
        migrations.CreateModel(
            name='EggPOSManualPrebookingEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('action', models.CharField(max_length=35)),
                ('details', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ('actor', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='egg_manual_prebooking_events', to=settings.AUTH_USER_MODEL)),
                ('booking', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='events', to='api.eggposmanualprebooking')),
            ],
            options={'ordering': ('created_at', 'id')},
        ),
        migrations.AddIndex(
            model_name='eggposmanualprebooking',
            index=models.Index(fields=['status', 'created_at'], name='ep_prebook_status_idx'),
        ),
    ]
