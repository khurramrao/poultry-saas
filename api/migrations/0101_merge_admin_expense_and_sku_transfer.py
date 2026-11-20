"""Merge the independent RayNoor expense-category and SKU-transfer migration branches.

This migration does not change tables or data; both 0100 migrations still run.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0100_admin_expense_packing_transport_social"),
        ("api", "0100_egg_pos_sku_transfer"),
    ]

    operations = []
