from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator

from django.db import models
from django.utils import timezone


class Shed(models.Model):
    name = models.CharField(max_length=100)
    shed_type = models.CharField(
        max_length=20,
        choices=[
            ("meat", "Meat"),
            ("layer", "Layer"),
            ("goat", "Goat"),
        ],
    )

    def __str__(self):
        return f"{self.name} ({self.shed_type})"


class Device(models.Model):
    device_id = models.CharField(
        max_length=100,
        unique=True,
    )

    shed = models.ForeignKey(
        Shed,
        on_delete=models.CASCADE,
    )

    is_active = models.BooleanField(
        default=True,
        help_text="Disabled devices will be ignored by the dashboard.",
    )

    @property
    def display_name(self):
        """
        Converts:
        esp32_shed_1  -> DEV-1
        esp32_shed_2  -> DEV-2
        esp32_shed_3  -> DEV-3
        esp32_shed_15 -> DEV-15

        Other device IDs are displayed unchanged.
        """
        prefix = "esp32_shed_"

        if self.device_id.startswith(prefix):
            device_number = self.device_id[len(prefix):]

            if device_number.isdigit():
                return f"DEV-{int(device_number)}"

        return self.device_id

    def __str__(self):
        return self.device_id


class Batch(models.Model):
    BATCH_TYPE_CHOICES = [
        ("meat", "Meat"),
        ("layer", "Layer"),
    ]

    shed = models.ForeignKey(
        Shed,
        on_delete=models.CASCADE,
    )

    batch_type = models.CharField(
        max_length=20,
        choices=BATCH_TYPE_CHOICES,
        default="meat",
    )

    batch_number = models.CharField(max_length=50)
    start_date = models.DateField()
    starting_age_days = models.PositiveIntegerField(default=0)
    bird_count_initial = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    STATUS_CHOICES = [
        ("active", "Active"),
        ("sold", "Sold"),
        ("closed", "Closed"),
    ]

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="active",
    )

    end_date = models.DateField(
        null=True,
        blank=True,
    )

    def __str__(self):
        return (
            f"{self.batch_type.title()} Batch "
            f"#{self.batch_number} - {self.shed.name}"
        )


class SensorData(models.Model):
    device = models.ForeignKey(
        Device,
        on_delete=models.CASCADE,
    )

    temperature = models.FloatField(
        null=True,
        blank=True,
    )

    humidity = models.FloatField(
        null=True,
        blank=True,
    )

    light_percent = models.IntegerField(default=0)
    ldr_raw = models.IntegerField(default=0)
    ammonia_raw = models.IntegerField(default=0)
    sensor_error = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.device.device_id} | {self.created_at}"


class MortalityRecord(models.Model):
    batch = models.ForeignKey(
        Batch,
        on_delete=models.CASCADE,
    )

    date = models.DateField()
    count = models.PositiveIntegerField(default=0)

    notes = models.TextField(
        blank=True,
        null=True,
    )

    created_at = models.DateTimeField(
        default=timezone.now,
    )

    def __str__(self):
        return (
            f"{self.batch.batch_number} - "
            f"{self.date} - {self.count}"
        )


class VaccineSchedule(models.Model):
    shed_type = models.CharField(
        max_length=20,
        choices=[
            ("meat", "Meat"),
            ("eggs", "Eggs"),
        ],
    )

    vaccine_name = models.CharField(max_length=100)
    day_number = models.PositiveIntegerField()

    notes = models.TextField(
        blank=True,
        null=True,
    )

    class Meta:
        ordering = [
            "day_number",
            "vaccine_name",
        ]

    def __str__(self):
        return (
            f"{self.shed_type} - "
            f"Day {self.day_number} - "
            f"{self.vaccine_name}"
        )


class VaccineRecord(models.Model):
    STATUS_CHOICES = [
        ("due", "Due"),
        ("done", "Done"),
        ("overdue", "Overdue"),
    ]

    batch = models.ForeignKey(
        Batch,
        on_delete=models.CASCADE,
    )

    vaccine_name = models.CharField(max_length=100)
    scheduled_day = models.PositiveIntegerField()
    due_date = models.DateField()

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="due",
    )

    given_date = models.DateField(
        blank=True,
        null=True,
    )

    notes = models.TextField(
        blank=True,
        null=True,
    )

    class Meta:
        ordering = [
            "due_date",
            "scheduled_day",
            "vaccine_name",
        ]

        unique_together = (
            "batch",
            "vaccine_name",
            "scheduled_day",
        )

    def __str__(self):
        return (
            f"{self.batch.batch_number} - "
            f"{self.vaccine_name} - "
            f"{self.status}"
        )


class RelayChannel(models.Model):
    LOAD_TYPE_CHOICES = [
        ("normal", "Normal Output"),
        ("motor", "Motor / Fan Output"),
    ]

    device = models.ForeignKey(
        Device,
        on_delete=models.CASCADE,
        related_name="relay_channels",
    )

    channel_number = models.PositiveSmallIntegerField(
        validators=[
            MinValueValidator(1),
            MaxValueValidator(16),
        ],
        help_text="Physical relay-board channel from 1 to 16.",
    )

    gpio_pin = models.PositiveSmallIntegerField(
        validators=[
            MinValueValidator(0),
            MaxValueValidator(39),
        ],
    )

    name = models.CharField(
        max_length=100,
        help_text="Example: Shed Light 1, Exhaust Fan, Water Motor.",
    )

    load_type = models.CharField(
        max_length=20,
        choices=LOAD_TYPE_CHOICES,
        default="normal",
    )

    is_enabled = models.BooleanField(
        default=True,
        help_text="Disabled channels cannot be operated from the dashboard.",
    )

    # State requested by Django
    desired_state = models.BooleanField(
        default=False,
    )

    # State most recently reported by the ESP32
    actual_state = models.BooleanField(
        default=False,
    )

    commanded_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    reported_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="relay_commands",
    )

    last_error = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )

    AUTOMATION_TYPE_CHOICES = [
        ("manual", "Manual Only"),
        ("daily", "Daily ON/OFF Schedule"),
        ("sensor_schedule", "Sensor + Schedule"),
        ("repeating", "Repeating Cycle"),
    ]

    automation_type = models.CharField(
        max_length=30,
        choices=AUTOMATION_TYPE_CHOICES,
        default="manual",
        help_text="Automation mode for this individual relay output.",
    )

    schedule_start_time = models.TimeField(
        null=True,
        blank=True,
        help_text=(
            "Daily ON time, sensor automation start time, or repeating-cycle anchor."
        ),
    )

    schedule_end_time = models.TimeField(
        null=True,
        blank=True,
        help_text=(
            "Daily OFF time, sensor force-OFF time, or optional repeating-cycle end time."
        ),
    )

    sensor_on_threshold = models.PositiveSmallIntegerField(
        default=60,
        validators=[
            MinValueValidator(0),
            MaxValueValidator(100),
        ],
        help_text="Sensor + Schedule: turn ON at or below this light percentage.",
    )

    sensor_off_threshold = models.PositiveSmallIntegerField(
        default=70,
        validators=[
            MinValueValidator(0),
            MaxValueValidator(100),
        ],
        help_text="Sensor + Schedule: turn OFF at or above this light percentage.",
    )

    repeat_interval_minutes = models.PositiveIntegerField(
        default=240,
        validators=[
            MinValueValidator(1),
            MaxValueValidator(1440),
        ],
        help_text="Repeating Cycle: minutes between cycle starts. 240 = every 4 hours.",
    )

    run_duration_minutes = models.PositiveIntegerField(
        default=30,
        validators=[
            MinValueValidator(1),
            MaxValueValidator(1440),
        ],
        help_text="Repeating Cycle: how long the relay stays ON each cycle.",
    )

    manual_override_state = models.BooleanField(
        null=True,
        blank=True,
        help_text="Temporary manual state while an automation mode is active.",
    )

    manual_override_started_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    manual_override_until = models.DateTimeField(
        null=True,
        blank=True,
    )

    manual_override_allow_outside_schedule = models.BooleanField(
        default=False,
        help_text=(
            "True only for an explicit temporary after-hours manual command."
        ),
    )

    command_source = models.CharField(
        max_length=20,
        choices=[
            ("manual", "Manual"),
            ("automation", "Automation"),
        ],
        default="manual",
    )

    class Meta:
        ordering = [
            "device_id",
            "channel_number",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=["device", "channel_number"],
                name="unique_relay_channel_per_device",
            ),
            models.UniqueConstraint(
                fields=["device", "gpio_pin"],
                name="unique_relay_gpio_per_device",
            ),
        ]

    @property
    def display_name(self):
        return f"Relay {self.channel_number}: {self.name}"

    def __str__(self):
        return (
            f"{self.device.display_name} - "
            f"CH{self.channel_number} - "
            f"{self.name}"
        )