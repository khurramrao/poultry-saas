from django.db import models


class Shed(models.Model):
    name = models.CharField(max_length=100)
    shed_type = models.CharField(
        max_length=20,
        choices=[
            ('meat', 'Meat'),
            ('layer', 'Layer'),
        ]
    )

    def __str__(self):
        return f"{self.name} ({self.shed_type})"


class Device(models.Model):
    device_id = models.CharField(max_length=100, unique=True)
    shed = models.ForeignKey(Shed, on_delete=models.CASCADE)

    def __str__(self):
        return self.device_id


class Batch(models.Model):
    shed = models.ForeignKey(Shed, on_delete=models.CASCADE)
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

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")
    end_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return f"{self.batch_number} - {self.shed.name}"


class SensorData(models.Model):
    device = models.ForeignKey(Device, on_delete=models.CASCADE)
    temperature = models.FloatField(null=True, blank=True)
    humidity = models.FloatField(null=True, blank=True)
    light_percent = models.IntegerField(default=0)
    ldr_raw = models.IntegerField(default=0)
    ammonia_raw = models.IntegerField(default=0)
    sensor_error = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.device.device_id} | {self.created_at}"


class MortalityRecord(models.Model):
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE)
    date = models.DateField()
    count = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.batch.batch_number} - {self.date} - {self.count}"


class VaccineSchedule(models.Model):
    shed_type = models.CharField(
        max_length=20,
        choices=[
            ('meat', 'Meat'),
            ('eggs', 'Eggs'),
        ]
    )
    vaccine_name = models.CharField(max_length=100)
    day_number = models.PositiveIntegerField()
    notes = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ['day_number', 'vaccine_name']

    def __str__(self):
        return f"{self.shed_type} - Day {self.day_number} - {self.vaccine_name}"


class VaccineRecord(models.Model):
    STATUS_CHOICES = [
        ('due', 'Due'),
        ('done', 'Done'),
        ('overdue', 'Overdue'),
    ]

    batch = models.ForeignKey(Batch, on_delete=models.CASCADE)
    vaccine_name = models.CharField(max_length=100)
    scheduled_day = models.PositiveIntegerField()
    due_date = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='due')
    given_date = models.DateField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ['due_date', 'scheduled_day', 'vaccine_name']
        unique_together = ('batch', 'vaccine_name', 'scheduled_day')

    def __str__(self):
        return f"{self.batch.batch_number} - {self.vaccine_name} - {self.status}"