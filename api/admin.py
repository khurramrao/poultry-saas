from django.contrib import admin
from api.models.sensor import (
    Shed,
    Device,
    Batch,
    SensorData,
    MortalityRecord,
    VaccineSchedule,
    VaccineRecord,
)
from api.models.temperature import TemperatureRule
from api.models.sales import SaleRecord

@admin.register(Shed)
class ShedAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'shed_type')
    list_filter = ('shed_type',)
    search_fields = ('name',)


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ('id', 'device_id', 'shed')
    list_filter = ('shed',)
    search_fields = ('device_id',)


@admin.register(Batch)
class BatchAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'batch_number',
        'shed',
        'start_date',
        'starting_age_days',
        'bird_count_initial',
        'is_active',
    )
    list_filter = ('shed', 'is_active')
    search_fields = ('batch_number', 'shed__name')


@admin.register(SensorData)
class SensorDataAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'device',
        'temperature',
        'humidity',
        'light_percent',
        'ammonia_raw',
        'sensor_error',
        'created_at',
    )
    list_filter = ('device', 'sensor_error', 'created_at')
    search_fields = ('device__device_id',)
    ordering = ('-created_at',)


@admin.register(MortalityRecord)
class MortalityRecordAdmin(admin.ModelAdmin):
    list_display = ('id', 'batch', 'date', 'count')
    list_filter = ('batch', 'date')
    search_fields = ('batch__batch_number',)


@admin.register(VaccineSchedule)
class VaccineScheduleAdmin(admin.ModelAdmin):
    list_display = ('id', 'shed_type', 'vaccine_name', 'day_number')
    list_filter = ('shed_type',)
    search_fields = ('vaccine_name',)


@admin.register(VaccineRecord)
class VaccineRecordAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'batch',
        'vaccine_name',
        'scheduled_day',
        'due_date',
        'status',
        'given_date',
    )
    list_filter = ('batch', 'status', 'due_date')
    search_fields = ('batch__batch_number', 'vaccine_name')

admin.site.register(TemperatureRule)
admin.site.register(SaleRecord)
