from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0048_weight_only_sales_reconciliation_and_payouts"),
    ]

    operations = [
        migrations.AddField(
            model_name="relaychannel",
            name="automation_type",
            field=models.CharField(
                choices=[
                    ("manual", "Manual Only"),
                    ("daily", "Daily ON/OFF Schedule"),
                    ("sensor_schedule", "Sensor + Schedule"),
                    ("repeating", "Repeating Cycle"),
                ],
                default="manual",
                help_text="Automation mode for this individual relay output.",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="relaychannel",
            name="schedule_start_time",
            field=models.TimeField(
                blank=True,
                help_text="Daily ON time, sensor automation start time, or repeating-cycle anchor.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="relaychannel",
            name="schedule_end_time",
            field=models.TimeField(
                blank=True,
                help_text="Daily OFF time, sensor force-OFF time, or optional repeating-cycle end time.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="relaychannel",
            name="sensor_on_threshold",
            field=models.PositiveSmallIntegerField(
                default=60,
                help_text="Sensor + Schedule: turn ON at or below this light percentage.",
                validators=[
                    django.core.validators.MinValueValidator(0),
                    django.core.validators.MaxValueValidator(100),
                ],
            ),
        ),
        migrations.AddField(
            model_name="relaychannel",
            name="sensor_off_threshold",
            field=models.PositiveSmallIntegerField(
                default=70,
                help_text="Sensor + Schedule: turn OFF at or above this light percentage.",
                validators=[
                    django.core.validators.MinValueValidator(0),
                    django.core.validators.MaxValueValidator(100),
                ],
            ),
        ),
        migrations.AddField(
            model_name="relaychannel",
            name="repeat_interval_minutes",
            field=models.PositiveIntegerField(
                default=240,
                help_text="Repeating Cycle: minutes between cycle starts. 240 = every 4 hours.",
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(1440),
                ],
            ),
        ),
        migrations.AddField(
            model_name="relaychannel",
            name="run_duration_minutes",
            field=models.PositiveIntegerField(
                default=30,
                help_text="Repeating Cycle: how long the relay stays ON each cycle.",
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(1440),
                ],
            ),
        ),
        migrations.AddField(
            model_name="relaychannel",
            name="manual_override_state",
            field=models.BooleanField(
                blank=True,
                help_text="Temporary manual state while an automation mode is active.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="relaychannel",
            name="manual_override_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="relaychannel",
            name="manual_override_until",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="relaychannel",
            name="manual_override_allow_outside_schedule",
            field=models.BooleanField(
                default=False,
                help_text="True only for an explicit temporary after-hours manual command.",
            ),
        ),
        migrations.AddField(
            model_name="relaychannel",
            name="command_source",
            field=models.CharField(
                choices=[("manual", "Manual"), ("automation", "Automation")],
                default="manual",
                max_length=20,
            ),
        ),
    ]
