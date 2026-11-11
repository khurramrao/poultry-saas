"""Add an outbound customer delivery category to the existing admin expense dropdown.

Inbound freight is already captured by the purchase/transfer transport fields and
must remain part of FIFO landed inventory cost, NOT a second general expense.
"""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("api", "0092_retail_inventory_writeoffs")]

    operations = [
        migrations.AlterField(
            model_name="eggposadminexpense",
            name="category",
            field=models.CharField(max_length=24, choices=[
                ("rent", "Office / Shop Rent"),
                ("utilities", "Electricity / Utilities"),
                ("internet", "Internet / Phone"),
                ("stationery", "Stationery / Printing"),
                ("supplies", "Office Consumables"),
                ("repair", "Repairs & Maintenance"),
                ("marketing", "Marketing / Advertising"),
                ("delivery", "Customer Delivery / Courier Charges"),
                ("professional", "Professional / Admin Fees"),
                ("equipment", "Equipment (Fixed Asset)"),
                ("other", "Other General Expense"),
            ]),
        ),
    ]
