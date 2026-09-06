from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0046_goat_pedigree_breeding"),
    ]

    operations = [
        migrations.AddField(
            model_name="goat",
            name="date_of_birth_is_estimated",
            field=models.BooleanField(
                default=False,
                help_text="Tick when the date of birth is estimated rather than known exactly.",
            ),
        ),
    ]
