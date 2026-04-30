from django.db import models
from api.models.sensor import Batch
from datetime import date


class SaleRecord(models.Model):
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE)
    sale_date = models.DateField(default=date.today)

    birds_sold = models.IntegerField()
    average_weight_kg = models.FloatField(null=True, blank=True)
    rate_per_kg = models.FloatField(null=True, blank=True)

    notes = models.TextField(blank=True)

    @property
    def total_amount(self):
        if self.average_weight_kg and self.rate_per_kg and self.birds_sold:
            return self.birds_sold * self.average_weight_kg * self.rate_per_kg
        return 0

    def __str__(self):
        return f"Batch {self.batch.batch_number} - Sold {self.birds_sold}"