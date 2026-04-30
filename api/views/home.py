import json
from datetime import date, timedelta
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt

from api.models.sensor import (
    Batch,
    Device,
    MortalityRecord,
    SensorData,
    Shed,
    VaccineRecord,
    VaccineSchedule,

)

from api.models.temperature import TemperatureRule
from api.models.sales import SaleRecord


def home(request):
    return render(request, "api/home.html")