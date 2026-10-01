"""Server-side stepping for repeating note reminders.

Mirrors ``_normalizeRepeat`` / ``_advanceRecurring`` in static/js/notes.js so a
repeating reminder keeps firing when no browser tab is open (e.g. reminders
delivered to a phone via ntfy). Stepping happens in the user's wall-clock
time zone (``ODYSSEUS_TIMEZONE``, falling back to the server's), so a weekly
12:15 reminder stays at 12:15 across DST changes.

Repeat format (same as the frontend):
  none | daily | yearly | weekly:W | monthly:day:D | monthly:nth:N:W | monthly:last:W
  W = 0-6 (Sun..Sat). Legacy bare "weekly", "monthly", "monthly_nth_weekday"
  and "monthly_last_weekday" are normalized from the original due date.
"""

import calendar
import os
import re
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Optional


def local_tz() -> tzinfo:
    name = (os.environ.get("ODYSSEUS_TIMEZONE") or "").strip()
    if name:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name)
        except Exception:
            pass
    return datetime.now().astimezone().tzinfo


def parse_due(value: str) -> Optional[datetime]:
    """Parse a note due_date: '...Z' is UTC, naive is server-local. Returns UTC."""
    if not value:
        return None
    try:
        if value.endswith("Z"):
            return datetime.fromisoformat(value[:-1]).replace(tzinfo=timezone.utc)
        d = datetime.fromisoformat(value)
        if d.tzinfo is None:
            d = d.astimezone()
        return d.astimezone(timezone.utc)
    except ValueError:
        return None


def _js_weekday(d: datetime) -> int:
    """JS getDay(): Sunday=0 .. Saturday=6."""
    return (d.weekday() + 1) % 7


def normalize_repeat(repeat: str, original: datetime) -> str:
    if not repeat or repeat == "none":
        return "none"
    if repeat in ("daily", "yearly") or re.match(r"^(weekly|monthly):", repeat):
        return repeat
    wd = _js_weekday(original)
    if repeat == "weekly":
        return f"weekly:{wd}"
    if repeat == "monthly":
        return f"monthly:day:{original.day}"
    if repeat == "monthly_nth_weekday":
        return f"monthly:nth:{(original.day + 6) // 7}:{wd}"
    if repeat == "monthly_last_weekday":
        return f"monthly:last:{wd}"
    return repeat


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> int:
    first_wd = (calendar.weekday(year, month, 1) + 1) % 7
    day = 1 + (weekday - first_wd) % 7 + (n - 1) * 7
    if day > calendar.monthrange(year, month)[1]:
        day -= 7
    return day


def _last_weekday(year: int, month: int, weekday: int) -> int:
    last = calendar.monthrange(year, month)[1]
    last_wd = (calendar.weekday(year, month, last) + 1) % 7
    return last - (last_wd - weekday) % 7


def _step(d: datetime, norm: str) -> Optional[datetime]:
    """One recurrence step on a naive local wall-clock datetime."""
    if norm == "daily":
        return d + timedelta(days=1)
    if norm == "yearly":
        try:
            return d.replace(year=d.year + 1)
        except ValueError:  # Feb 29
            return d.replace(year=d.year + 1, day=28)
    parts = norm.split(":")
    if parts[0] == "weekly":
        delta = (int(parts[1]) - _js_weekday(d)) % 7 or 7
        return d + timedelta(days=delta)
    if parts[0] == "monthly":
        year, month = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
        if parts[1] == "day":
            day = min(int(parts[2]), calendar.monthrange(year, month)[1])
        elif parts[1] == "nth":
            day = _nth_weekday(year, month, int(parts[3]), int(parts[2]))
        elif parts[1] == "last":
            day = _last_weekday(year, month, int(parts[2]))
        else:
            return None
        return d.replace(year=year, month=month, day=day)
    return None


def next_due(due_date: str, repeat: str, now: Optional[datetime] = None,
             tz: Optional[tzinfo] = None) -> Optional[str]:
    """Next due_date strictly after ``now`` as a UTC '...Z' string, or None."""
    due = parse_due(due_date)
    if due is None or not repeat or repeat == "none":
        return None
    tz = tz or local_tz()
    now = now or datetime.now(timezone.utc)
    local = due.astimezone(tz).replace(tzinfo=None)
    norm = normalize_repeat(repeat, local)
    if norm == "none":
        return None
    d = _step(local, norm)
    guard = 5000
    while d is not None and d.replace(tzinfo=tz).astimezone(timezone.utc) <= now:
        guard -= 1
        if guard <= 0:
            return None
        d = _step(d, norm)
    if d is None:
        return None
    utc = d.replace(tzinfo=tz).astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
