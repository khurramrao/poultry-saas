"""Optional preferred delivery window and quoted unit prices for manual pre-bookings."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("api", "0090_manual_prebooking_customer_link")]

    operations = [
        migrations.AddField(
            model_name="eggposmanualprebooking",
            name="delivery_time_from",
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="eggposmanualprebooking",
            name="delivery_time_to",
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="eggposmanualprebookingline",
            name="quoted_unit_price",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
    ]
