from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver
from django.utils import timezone

from api.models.investors import UserActivityLog, UserActivityStatus


def get_client_ip(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")

    if forwarded_for:
        return forwarded_for.split(",")[0].strip()

    return request.META.get("REMOTE_ADDR")


def get_user_agent(request):
    return request.META.get("HTTP_USER_AGENT", "")[:255]


@receiver(user_logged_in)
def log_user_login(sender, request, user, **kwargs):
    ip_address = get_client_ip(request)
    user_agent = get_user_agent(request)

    UserActivityLog.objects.create(
        user=user,
        event_type="login",
        ip_address=ip_address,
        user_agent=user_agent,
    )

    UserActivityStatus.objects.update_or_create(
        user=user,
        defaults={
            "last_seen": timezone.now(),
            "last_ip": ip_address,
            "last_user_agent": user_agent,
        }
    )


@receiver(user_logged_out)
def log_user_logout(sender, request, user, **kwargs):
    if not user:
        return

    UserActivityLog.objects.create(
        user=user,
        event_type="logout",
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )