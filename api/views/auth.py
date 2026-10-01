from django.contrib import messages
from django.contrib.auth.views import LoginView
from django.contrib.messages import get_messages
from django.urls import reverse


class FarmLoginView(LoginView):
    template_name = "api/login.html"

    def _clear_stale_farm_permission_messages(self):
        """
        A pure Egg POS salesperson can have old permission-denied messages
        queued in the Django session from earlier attempts to open farm pages.

        Once the salesperson is successfully logging into Egg POS, those old
        farm-only warnings should not be shown on the POS dashboard.

        Keep any unrelated messages.
        """
        stale_messages = {
            "You are not allowed to view feed entries.",
            "You are not allowed to view medicine entries.",
            "You are not allowed to view expenses.",
        }

        existing_messages = list(get_messages(self.request))

        for message in existing_messages:
            message_text = str(message)

            if message_text not in stale_messages:
                messages.add_message(
                    self.request,
                    message.level,
                    message_text,
                    extra_tags=message.extra_tags,
                )

    def get_success_url(self):
        user = self.request.user

        # Django Admin/Staff continue to the normal farm dashboard.
        # A normal user who was explicitly granted active Egg POS sale access
        # enters the POS directly after login.
        if not user.is_superuser and not user.is_staff:
            try:
                access = user.egg_pos_access
            except Exception:
                access = None

            if (
                access
                and access.is_active
                and (access.can_sell or access.can_manage)
            ):
                self._clear_stale_farm_permission_messages()
                return reverse("egg_pos_dashboard")

        return super().get_success_url()
