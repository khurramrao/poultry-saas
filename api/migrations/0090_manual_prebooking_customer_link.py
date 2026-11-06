"""Optional saved customer link for manually entered pre-bookings.

Historical bookings remain free-text; no automatic matching/merging.
"""
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [
        ("api", "0089_retail_ghee_honey"),
    ]
    operations = [
        migrations.AddField(
            model_name="eggposmanualprebooking",
            name="customer",
            field=models.ForeignKey(
                to="api.eggposcustomer", on_delete=django.db.models.deletion.SET_NULL,
                null=True, blank=True, related_name="manual_prebookings",
            ),
        ),
    ]
