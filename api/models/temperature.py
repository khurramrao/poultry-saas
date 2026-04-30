from django.db import models

class TemperatureRule(models.Model):
    SHED_TYPES = [
        ("meat", "Meat"),
        ("layer", "Layer"),
    ]

    shed_type = models.CharField(max_length=20, choices=SHED_TYPES)

    min_age_days = models.IntegerField()
    max_age_days = models.IntegerField()

    low_temp = models.FloatField()
    high_temp = models.FloatField()

    def __str__(self):
        return f"{self.shed_type} | {self.min_age_days}-{self.max_age_days} days"