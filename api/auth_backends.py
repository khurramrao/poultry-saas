"""Safe case-insensitive authentication for existing RayNoor Django users.

Stored usernames, passwords, permissions, and account IDs stay unchanged.
If several usernames differ only by letter case, users must enter the exact
stored spelling; an ambiguous case-insensitive lookup is never guessed.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class UniqueCaseInsensitiveUsernameBackend(ModelBackend):
    """Authenticate with any username capitalization when the match is unique."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        User = get_user_model()
        if username is None:
            username = kwargs.get(User.USERNAME_FIELD)
        if username is None or password is None:
            return None

        username = str(username).strip()
        if not username:
            return None

        field = User.USERNAME_FIELD
        try:
            # Preserve exact spelling for legacy accounts whose usernames only
            # differ by capitalization; do not silently select another account.
            user = User._default_manager.get(**{field: username})
        except User.DoesNotExist:
            possible = list(
                User._default_manager.filter(**{f"{field}__iexact": username})
                .order_by("pk")[:2]
            )
            if len(possible) != 1:
                User().set_password(password)  # costly dummy hash, as in Django
                return None
            user = possible[0]
        except User.MultipleObjectsReturned:
            User().set_password(password)
            return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
