from django.db import models
from django.contrib.auth.models import User
from decimal import Decimal
from django.db import models
from api.models.sensor import Batch

class InvestorProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="investor_profile")
    phone_number = models.CharField(max_length=15, blank=True)

    def __str__(self):
        return self.user.username

class InvestorAllocation(models.Model):
    batch = models.ForeignKey('Batch', on_delete=models.CASCADE, related_name="allocations")
    investor = models.ForeignKey(InvestorProfile, on_delete=models.CASCADE, related_name="allocations")
    birds_owned = models.PositiveIntegerField(help_text="Number of birds this investor funded in this batch")

    class Meta:
        # Prevents assigning the same investor twice to the same batch
        unique_together = ('batch', 'investor')

    def __str__(self):
        return f"{self.investor.user.username} - {self.birds_owned} Birds"




class UserFeedStatus(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    last_seen_feed_time = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.user.username




class BatchCost(models.Model):
    batch = models.OneToOneField(Batch, on_delete=models.CASCADE)

    chick_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    carriage_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    feed_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    medicine_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    notes = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def total_cogs(self):
        return (
            self.chick_cost +
            self.carriage_cost +
            self.feed_cost +
            self.medicine_cost
        )

    def __str__(self):
        return f"Batch {self.batch.batch_number} COGS"


class FeedEntry(models.Model):
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE)
    entry_date = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Batch {self.batch.batch_number} Feed - Rs {self.amount}"


class MedicineEntry(models.Model):
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE)
    entry_date = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Batch {self.batch.batch_number} Medicine - Rs {self.amount}"
