"""Management-only, read-only SKU breakdown for the RayNoor Retail POS."""
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone

from api.services.egg_pos import can_manage
from api.services.egg_pos_sku_report import make_report


@login_required
def sku_report(request):
    # COGS and per-SKU gross profit are managerial data, not salesperson data.
    if not can_manage(request.user):
        messages.error(request, 'Only Retail POS management can view SKU sales and bookings.')
        return redirect('egg_pos_dashboard')

    today = timezone.localdate()
    start = today.replace(day=1)
    end = today
    category = request.GET.get('category', 'all')
    if category not in ('all', 'egg', 'ghee', 'honey'):
        category = 'all'
    error = None
    try:
        if request.GET.get('from'):
            start = date.fromisoformat(request.GET['from'])
        if request.GET.get('to'):
            end = date.fromisoformat(request.GET['to'])
        if start > end:
            raise ValueError('Start date must be on or before end date.')
        if end > today:
            raise ValueError('End date cannot be in the future.')
    except ValueError as exc:
        error = str(exc) if 'Start date' in str(exc) or 'End date' in str(exc) else 'Enter valid dates (YYYY-MM-DD).'
        start, end = today.replace(day=1), today

    return render(request, 'api/egg_pos_sku_report.html', {
        'report': make_report(start, end, category),
        'start': start, 'end': end, 'category': category,
        'filter_error': error, 'as_of': today,
    })
