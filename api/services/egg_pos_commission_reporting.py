"""Commission date coverage and forward-only loss settlement.

Posted periods are immutable accounting snapshots. New dates must begin the day
following the latest posted period. Losses become a carryforward, not a negative
commission or an adjustment to previously earned/paid commission.
"""
from datetime import timedelta
from decimal import Decimal

ZERO = Decimal("0.00")


def money_amount(value):
    """Round monetary outputs to two places using the project's money helper."""
    from api.services.accounting import money
    return money(value)


def next_commission_start(last_period):
    return last_period.period_end + timedelta(days=1) if last_period else None


def commission_with_loss(snapshot, incoming_loss=ZERO):
    """Return the forward-only commission calculation for one consecutive period.

    `net_after_expenses` is SIGNED, unlike the existing commission_preview's
    `commissionable_profit` which floors negative profit at zero.
    Historical posted periods are never recalculated here.
    """
    incoming = money_amount(incoming_loss or ZERO)
    if incoming < ZERO:
        raise ValueError("Carried commission loss cannot be negative.")
    net = money_amount(snapshot["net_after_expenses"])
    rate = Decimal(snapshot["commission_percent"] or ZERO)
    if rate < ZERO or rate > Decimal("100"):
        raise ValueError("Commission percentage must be between 0 and 100.")
    amount_base = money_amount(max(net - incoming, ZERO))
    outgoing = money_amount(max(incoming - net, ZERO))
    return {
        "net_profit": net,
        "loss_in": incoming,
        "commissionable_after_loss": amount_base,
        "loss_out": outgoing,
        "commission_amount": money_amount(amount_base * rate / Decimal("100")),
    }


def unposted_date_ranges(start_date, end_date, posted_periods):
    """Return inclusive, disjoint date ranges outside any posted period."""
    if end_date < start_date:
        return []
    coverage = []
    for period in posted_periods:
        lo = max(start_date, period.period_start)
        hi = min(end_date, period.period_end)
        if lo <= hi:
            coverage.append((lo, hi))
    coverage.sort()
    remaining = []
    cursor = start_date
    one_day = timedelta(days=1)
    for lo, hi in coverage:
        if lo > cursor:
            remaining.append((cursor, lo - one_day))
        if hi >= cursor:
            cursor = hi + one_day
    if cursor <= end_date:
        remaining.append((cursor, end_date))
    return remaining
