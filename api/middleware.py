from datetime import timedelta

from django.utils import timezone

from api.models.investors import UserActivityStatus


def get_client_ip(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")

    if forwarded_for:
        return forwarded_for.split(",")[0].strip()

    return request.META.get("REMOTE_ADDR")


def get_user_agent(request):
    return request.META.get("HTTP_USER_AGENT", "")[:255]


class UserActivityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            now = timezone.now()

            status, created = UserActivityStatus.objects.get_or_create(
                user=request.user
            )

            if not status.last_seen or now - status.last_seen > timedelta(minutes=1):
                status.last_seen = now
                status.last_ip = get_client_ip(request)
                status.last_user_agent = get_user_agent(request)
                status.save(
                    update_fields=[
                        "last_seen",
                        "last_ip",
                        "last_user_agent",
                        "updated_at",
                    ]
                )

        response = self.get_response(request)
        return response