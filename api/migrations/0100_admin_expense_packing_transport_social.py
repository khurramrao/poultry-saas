"""Add RayNoor Retail POS expense categories for packaging, stock movement and social advertising."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("api", "0099_salary_advance_correction")]
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
                ("packing_material", "Packing Material"),
                ("repair", "Repairs & Maintenance"),
                ("marketing", "Marketing / Advertising"),
                ("social_media_marketing", "Social Media Marketing"),
                ("delivery", "Customer Delivery / Courier Charges"),
                ("stock_transport", "Stock Transport Charges"),
                ("professional", "Professional / Admin Fees"),
                ("equipment", "Equipment (Fixed Asset)"),
                ("other", "Other General Expense"),
            ]),
        ),
    ]
