from django.db import migrations


def convert_motor_sensor_rules_to_celsius(apps, schema_editor):
    RelayChannel = apps.get_model("api", "RelayChannel")

    RelayChannel.objects.filter(
        load_type="motor",
        automation_type="sensor_schedule",
    ).update(
        sensor_on_threshold=30,
        sensor_off_threshold=27,
    )


def reverse_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0056_egg_pos_customers_and_stock_guard"),
    ]

    operations = [
        migrations.RunPython(
            convert_motor_sensor_rules_to_celsius,
            reverse_noop,
        ),
    ]
