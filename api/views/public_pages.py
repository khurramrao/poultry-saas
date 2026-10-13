"""Public, read-only RayNoor informational pages.

These pages deliberately do not expose sensor data, investor balances, livestock
records or login-protected dashboards to anonymous visitors.
"""
from django.shortcuts import render


def goats_qurbani(request):
    return render(request, 'api/public_goats_qurbani.html')


def poultry_eggs(request):
    return render(request, 'api/public_poultry_eggs.html')


def smart_farming(request):
    return render(request, 'api/public_smart_farming.html')


def investor_participation(request):
    return render(request, 'api/public_investors.html')


def about_raynoor(request):
    return render(request, 'api/public_about.html')
