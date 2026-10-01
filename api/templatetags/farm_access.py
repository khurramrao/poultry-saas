from django import template

from api.access_control import get_farm_access_flags


register = template.Library()


@register.simple_tag
def farm_access_flags(user):
    """
    Return role/ownership flags for the farm sidebar.

    Important:
    An InvestorProfile by itself does NOT unlock investor menus.
    The user must have a real InvestorAllocation with birds_owned > 0.
    """
    return get_farm_access_flags(user)
