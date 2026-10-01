"""Server-side advancing of repeating note reminders (src/recurring_notes.py)."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.recurring_notes import next_due, normalize_repeat

TOR = ZoneInfo("America/Toronto")


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def test_weekly_keeps_local_wall_clock_across_dst():
    # Thu Oct 29 2026 12:15 EDT (16:15Z); DST ends Nov 1 -> next is 12:15 EST (17:15Z).
    assert next_due("2026-10-29T16:15:00.000Z", "weekly", now=_utc(2026, 10, 29, 16, 16), tz=TOR) \
        == "2026-11-05T17:15:00.000Z"


def test_catches_up_past_missed_slots():
    assert next_due("2026-09-29T18:15:00.000Z", "weekly", now=_utc(2026, 10, 14), tz=TOR) \
        == "2026-10-20T18:15:00.000Z"


def test_naive_due_date_is_server_local(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    import time
    time.tzset()
    assert next_due("2026-10-01T12:00", "daily", now=_utc(2026, 10, 1, 12, 1), tz=timezone.utc) \
        == "2026-10-02T12:00:00.000Z"


@pytest.mark.parametrize("repeat,expected", [
    ("daily", "2026-10-02T16:00:00.000Z"),
    ("weekly:1", "2026-10-05T16:00:00.000Z"),           # next Monday
    ("monthly", "2026-11-01T17:00:00.000Z"),            # day 1 -> Nov 1, EST after DST
    ("monthly:last:4", "2026-11-26T17:00:00.000Z"),     # last Thursday of next month
    ("yearly", "2027-10-01T16:00:00.000Z"),
])
def test_repeat_kinds(repeat, expected):
    # Thu Oct 1 2026 12:00 EDT
    assert next_due("2026-10-01T16:00:00.000Z", repeat, now=_utc(2026, 10, 1, 16, 1), tz=TOR) == expected


def test_non_repeating_and_bad_input_return_none():
    assert next_due("2026-10-01T16:00:00.000Z", "none") is None
    assert next_due("garbage", "weekly") is None


def test_normalize_matches_frontend_legacy_values():
    d = datetime(2026, 10, 15)  # Thursday, 3rd week
    assert normalize_repeat("weekly", d) == "weekly:4"
    assert normalize_repeat("monthly_nth_weekday", d) == "monthly:nth:3:4"
    assert normalize_repeat("monthly_last_weekday", d) == "monthly:last:4"


@pytest.fixture
def notes_db(tmp_path, monkeypatch):
    import core.database as cdb
    from src import builtin_actions

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    cdb.Base.metadata.create_all(engine, tables=[cdb.Note.__table__])
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(cdb, "SessionLocal", factory)
    monkeypatch.setattr(builtin_actions, "DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ODYSSEUS_TIMEZONE", "America/Toronto")
    import src.settings as st
    monkeypatch.setattr(st, "load_settings", lambda: {"reminder_channel": "ntfy"})
    sent = []
    outcome = {"ntfy_sent": True}

    async def fake_dispatch(**kw):
        sent.append(kw["title"])
        return dict(outcome)

    import routes.note_routes as nr
    monkeypatch.setattr(nr, "dispatch_reminder", fake_dispatch)
    return factory, sent, outcome


def test_scanner_fires_and_advances_repeating_notes(notes_db):
    from core.database import Note
    from src.builtin_actions import action_ping_notes

    factory, sent, _ = notes_db
    now = datetime.now(timezone.utc)
    db = factory()
    db.add_all([
        Note(id="due", owner="u", title="Reminder: Seminar", repeat="weekly",
             due_date=now.strftime("%Y-%m-%dT%H:%M:%S.000Z")),
        Note(id="stale", owner="u", title="Reminder: Resource Room", repeat="weekly",
             due_date="2026-01-06T19:15:00.000Z"),
        Note(id="once", owner="u", title="One-off", repeat="none",
             due_date="2026-01-06T19:15:00.000Z"),
    ])
    db.commit()
    db.close()

    msg, ok = asyncio.run(action_ping_notes(owner="u"))

    assert ok and sent == ["Reminder: Seminar"]
    db = factory()
    rows = {n.id: n.due_date for n in db.query(Note).all()}
    db.close()
    for nid in ("due", "stale"):
        nxt = datetime.fromisoformat(rows[nid].replace("Z", "+00:00"))
        assert now < nxt <= now + timedelta(days=7, hours=1)
    assert rows["once"] == "2026-01-06T19:15:00.000Z"


def test_failed_push_is_retried_not_advanced(notes_db, caplog):
    from core.database import Note
    from src.builtin_actions import TaskNoop, action_ping_notes

    factory, sent, outcome = notes_db
    outcome.update(ntfy_sent=False, ntfy_error="host does not resolve")
    due = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    db = factory()
    db.add(Note(id="due", owner="u", title="Reminder: Seminar", repeat="weekly", due_date=due))
    db.commit()
    db.close()

    with pytest.raises(TaskNoop):
        asyncio.run(action_ping_notes(owner="u"))
    assert "ntfy delivery failed" in caplog.text and "host does not resolve" in caplog.text

    outcome.update(ntfy_sent=True)          # next tick: ntfy is back
    msg, ok = asyncio.run(action_ping_notes(owner="u"))
    assert ok and sent == ["Reminder: Seminar", "Reminder: Seminar"]
    db = factory()
    assert db.query(Note).one().due_date != due
    db.close()
