from django.db import migrations
from django.db.models import Q


def mark_fan_relays_as_motor(apps, schema_editor):
    RelayChannel = apps.get_model("api", "RelayChannel")

    fan_qs = RelayChannel.objects.filter(
        Q(name__icontains="fan")
        | Q(name__icontains="exhaust")
    )

    # These outputs are physical fans, so they must use temperature automation,
    # not the normal light-percentage automation.
    fan_qs.update(
        load_type="motor",
        sensor_on_threshold=30,
        sensor_off_threshold=27,
    )


def reverse_noop(apps, schema_editor):
    # Do not blindly convert fans back to "normal" on rollback.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0057_motor_sensor_schedule_celsius"),
    ]

    operations = [
        migrations.RunPython(
            mark_fan_relays_as_motor,
            reverse_noop,
        ),
    ]
