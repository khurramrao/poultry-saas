"""Administrator-only self-contained loans/repayments screen."""
import uuid
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from api.models.egg_pos_admin_loan import EggPOSAdminLoanTransaction
from api.services.egg_pos import can_manage
from api.services.egg_pos_admin_loans import loan_outstanding, record_admin_loan


def _admin(user):
    return user.is_authenticated and (user.is_superuser or (user.is_staff and can_manage(user)))


@login_required
@require_http_methods(["GET", "POST"])
def admin_loans(request):
    if not _admin(request.user):
        return HttpResponseForbidden("Only Retail POS administrators may manage administrator loans.")
    users = [u for u in get_user_model().objects.filter(is_active=True).order_by('username')
             if u.is_superuser or (u.is_staff and can_manage(u))]
    if request.method == 'POST':
        try:
            if not request.user.has_usable_password() or not request.user.check_password(request.POST.get('admin_password') or ''):
                raise ValueError('Admin password verification failed.')
            admin_id = int(request.POST.get('administrator') or 0)
            if admin_id not in [u.pk for u in users]:
                raise ValueError('Select an authorized administrator.')
            row, duplicate = record_admin_loan(
                administrator_id=admin_id,
                kind=(request.POST.get('kind') or '').strip(),
                amount=request.POST.get('amount'),
                payment_source=(request.POST.get('payment_source') or '').strip(),
                transaction_date=timezone.localdate(),
                request_token=request.POST.get('request_token'),
                recorded_by=request.user,
                reference=request.POST.get('reference') or '',
                notes=request.POST.get('notes') or '',
            )
            if duplicate:
                messages.info(request, 'This loan transaction was already recorded; duplicate ignored.')
            else:
                messages.success(request, f"{row.get_kind_display()} — Rs {row.amount:,.2f} recorded.")
            return redirect('egg_pos_admin_loans')
        except (ValueError, TypeError, get_user_model().DoesNotExist) as exc:
            messages.error(request, str(exc))
    rows = list(EggPOSAdminLoanTransaction.objects.select_related('administrator','recorded_by')[:200])
    summary = [{'admin': u, 'balance': loan_outstanding(u.pk)} for u in users]
    return render(request, 'api/egg_pos_admin_loans.html', {
        'admins': users, 'rows': rows, 'summary': summary,
        'request_token': str(uuid.uuid4()),
        'today': timezone.localdate(),
    })
