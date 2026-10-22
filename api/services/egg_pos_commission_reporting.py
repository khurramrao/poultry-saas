"""Read-only commission reporting helpers.

A calendar overview must not re-post previously posted earnings. Posted periods
are immutable accounting snapshots; estimates cover only unposted dates.
"""
from datetime import timedelta


def unposted_date_ranges(start_date, end_date, posted_periods):
    """Return inclusive, disjoint date ranges not covered by posted periods.

    Periods may overlap the report bounds or each other. Missing dates are
    returned in chronological order. Does not mutate posted records.
    """
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
