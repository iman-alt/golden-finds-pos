"""
Time zones.

SQLite's datetime('now') records UTC. The shop runs on Kenyan time, three
hours ahead, so "today" in the shop starts at 21:00 UTC the day before.
Comparing a UTC timestamp's date with the shop's date quietly files every
sale made between midnight and 3am under the wrong day, and prints every
receipt three hours early.

So timestamps stay UTC in the database - one unambiguous clock - and are
turned into shop time in exactly two places: SQL date filters use
date(created_at, 'localtime'), and anything shown on screen goes through
to_local() below.
"""

from datetime import datetime, timezone


def to_local(value):
    """'2026-09-12 21:30:00' (UTC) -> '2026-09-13 00:30:00' (shop time)."""
    if not value:
        return value
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return value
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone().strftime("%Y-%m-%d %H:%M:%S")
