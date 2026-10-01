def is_farm_admin(user):
    return bool(
        user
        and getattr(user, "is_authenticated", False)
        and (user.is_superuser or user.is_staff)
    )


def has_poultry_investment(user):
    if not user or not getattr(user, "is_authenticated", False):
        return False

    from api.models.investors import InvestorAllocation

    return InvestorAllocation.objects.filter(
        investor__user=user,
        birds_owned__gt=0,
    ).exists()


def has_layer_investment(user):
    if not user or not getattr(user, "is_authenticated", False):
        return False

    from api.models.investors import InvestorAllocation

    return InvestorAllocation.objects.filter(
        investor__user=user,
        birds_owned__gt=0,
        batch__shed__shed_type="layer",
    ).exists()


def has_goat_ownership(user):
    if not user or not getattr(user, "is_authenticated", False):
        return False

    from api.models.goats import Goat

    return Goat.objects.filter(owner=user).exists()


def can_use_egg_pos(user):
    if not user or not getattr(user, "is_authenticated", False):
        return False

    if is_farm_admin(user):
        return True

    try:
        access = user.egg_pos_access
    except Exception:
        return False

    return bool(
        access.is_active
        and (access.can_sell or access.can_manage)
    )


def get_farm_access_flags(user):
    admin = is_farm_admin(user)

    # Admin gets every management menu without extra ownership queries.
    if admin:
        return {
            "is_admin": True,
            "has_poultry_investment": True,
            "has_layer_investment": True,
            "has_goat_ownership": True,
            "can_use_egg_pos": True,
        }

    return {
        "is_admin": False,
        "has_poultry_investment": has_poultry_investment(user),
        "has_layer_investment": has_layer_investment(user),
        "has_goat_ownership": has_goat_ownership(user),
        "can_use_egg_pos": can_use_egg_pos(user),
    }
