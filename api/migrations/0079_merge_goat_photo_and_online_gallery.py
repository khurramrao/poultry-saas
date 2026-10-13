"""Join the independent goat-photo and online-offer gallery migration branches.

No schema or data operations are required: both branches add different fields.
"""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0076_goat_photo"),
        ("api", "0078_online_offer_gallery_and_details"),
    ]

    operations = []
