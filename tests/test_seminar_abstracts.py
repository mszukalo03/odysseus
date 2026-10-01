"""Seminar abstract emails -> that week's seminar event (src/seminar_abstracts.py)."""
import asyncio
import email
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src import seminar_abstracts as sa

TOR = ZoneInfo("America/Toronto")

EMAIL = b"""From: Me <me@example.org>
To: me@home.example
Subject: Fwd: Physics Seminar - Oct 8 - Dr. Ada Lovelace
Message-ID: <abc123@example.org>
Date: Thu, 01 Oct 2026 14:05:00 -0400
Content-Type: text/html; charset=utf-8

<p>---------- Forwarded message ----------</p>
<p>Department Seminar, Thursday October 8, 12:30 pm, PAB 100</p>
<p>Speaker: Dr. Ada Lovelace, University of Somewhere</p>
<p>Title: Analytical Engines for Quantum Materials</p>
<p>Abstract: We present ... See <a href="https://doi.org/10.1103%2FPhysRevB.99.045123">our paper</a>
and doi:10.1038/s41586-020-2649-2.</p>
"""


def test_extract_dois_from_text_and_encoded_hrefs():
    msg = email.message_from_bytes(EMAIL)
    text, html = sa.message_parts(msg)
    assert sa.extract_dois(text, html) == ["10.1103/PhysRevB.99.045123", "10.1038/s41586-020-2649-2"]


def test_load_config_defaults_and_overrides():
    cfg = sa.load_config('{"event": "Colloquium", "subject_keywords": "Talk"}')
    assert cfg["event"] == "Colloquium" and cfg["subject_keywords"] == ["talk"]
    assert sa.load_config("not json")["event"] == "Seminar"


@pytest.fixture
def cal(monkeypatch, tmp_path):
    import core.database as cdb
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    cdb.Base.metadata.create_all(engine, tables=[
        cdb.CalendarCal.__table__, cdb.CalendarEvent.__table__, cdb.EmailAccount.__table__])
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(cdb, "SessionLocal", factory)
    monkeypatch.setattr(sa, "DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ODYSSEUS_TIMEZONE", "America/Toronto")
    db = factory()
    db.add(cdb.CalendarCal(id="msc", owner="me", name="MSc", source="local"))
    db.add(cdb.CalendarCal(id="other", owner="someone", name="MSc", source="local"))
    # Weekly Thursday 12:30 EDT, stored as naive UTC like the UI does.
    db.add(cdb.CalendarEvent(uid="series", calendar_id="msc", summary="Seminar", location="PAB",
                             dtstart=datetime(2026, 10, 1, 16, 30), dtend=datetime(2026, 10, 1, 18, 0),
                             is_utc=True, rrule="FREQ=WEEKLY", status="confirmed"))
    db.add(cdb.CalendarEvent(uid="theirs", calendar_id="other", summary="Seminar",
                             dtstart=datetime(2026, 10, 1, 16, 30), dtend=datetime(2026, 10, 1, 18, 0),
                             is_utc=True, rrule="FREQ=WEEKLY", status="confirmed"))
    db.commit()
    db.close()
    return factory


def test_find_series_is_owner_scoped(cal):
    db = cal()
    assert sa.find_series(db, "me", sa.load_config("")).uid == "series"
    assert sa.find_series(db, "me", sa.load_config('{"event": "Nope"}')) is None
    db.close()


def test_pick_occurrence_by_stated_date_and_fallback(cal):
    from core.database import CalendarEvent
    db = cal()
    series = db.query(CalendarEvent).get("series")
    received = datetime(2026, 10, 1, 18, 5, tzinfo=timezone.utc)
    assert sa.pick_occurrence(series, "2026-10-08", received, TOR) == datetime(2026, 10, 8, 16, 30)
    assert sa.pick_occurrence(series, "2026-10-09", received, TOR) is None   # not a seminar day
    assert sa.pick_occurrence(series, "", received, TOR) == datetime(2026, 10, 8, 16, 30)
    # After DST ends the stored UTC start shifts but the local date still matches.
    assert sa.pick_occurrence(series, "2026-11-05", received, TOR) == datetime(2026, 11, 5, 16, 30)
    db.close()


def _run_pipeline(cal, monkeypatch, llm_reply, crossref=None):
    import core.database as cdb
    db = cal()
    db.add(cdb.EmailAccount(id="acct", owner="me", name="Home", enabled=True))
    db.commit()
    db.close()
    notified = []

    async def fake_llm(**kw):
        return llm_reply

    async def fake_dispatch(**kw):
        notified.append(kw)
        return {}

    async def fake_crossref(title, speaker):
        return crossref

    import src.endpoint_resolver as er
    import src.llm_core as lc
    import routes.note_routes as nr
    monkeypatch.setattr(er, "resolve_endpoint", lambda *a, **k: ("http://llm/v1", "m", {}))
    monkeypatch.setattr(lc, "llm_call_async", fake_llm)
    monkeypatch.setattr(nr, "dispatch_reminder", fake_dispatch)
    monkeypatch.setattr(sa, "crossref_doi", fake_crossref)
    monkeypatch.setattr(sa, "_email_link", lambda uid: f"http://ody:7000/#email=INBOX:{uid}")
    monkeypatch.setattr(sa, "fetch_candidates", lambda acct, owner, kw, days, skip: [
        (k, "42", email.message_from_bytes(EMAIL)) for k in ["<abc123@example.org>"] if k not in skip])
    return notified


LLM_REPLY = ('<think>hmm</think>{"seminar_date": "2026-10-08", "speaker": "Dr. Ada Lovelace", '
             '"affiliation": "University of Somewhere", "title": "Analytical Engines for Quantum Materials", '
             '"summary": "A short summary.", "speaker_note": null}')


def test_run_detaches_occurrence_and_is_idempotent(cal, monkeypatch):
    from core.database import CalendarEvent
    from routes.calendar_routes import _expand_rrule

    notified = _run_pipeline(cal, monkeypatch, LLM_REPLY)
    result = asyncio.run(sa.run("me", ""))
    assert result.startswith("created 2026-10-08 Analytical Engines")

    db = cal()
    one_off = db.query(CalendarEvent).filter(CalendarEvent.origin == sa.ORIGIN).one()
    series = db.query(CalendarEvent).get("series")
    assert one_off.dtstart == datetime(2026, 10, 8, 16, 30) and one_off.dtend == datetime(2026, 10, 8, 18, 0)
    assert one_off.summary == "Seminar: Analytical Engines for Quantum Materials (Dr. Ada Lovelace)"
    assert one_off.location == "PAB" and one_off.rrule == ""
    assert "DOI: https://doi.org/10.1103/PhysRevB.99.045123" in one_off.description
    assert "Full email: http://ody:7000/#email=INBOX:42" in one_off.description
    assert "Speaker: Dr. Ada Lovelace (University of Somewhere)" in one_off.description
    starts = [o["dtstart"] for o in _expand_rrule(series, datetime(2026, 10, 1), datetime(2026, 10, 20))]
    assert starts == ["2026-10-01T16:30:00Z", "2026-10-15T16:30:00Z"]
    db.close()
    assert notified[0]["title"] == "Seminar 2026-10-08: Analytical Engines for Quantum Materials"

    # Second pass: message already processed -> nothing new.
    assert asyncio.run(sa.run("me", "")) == ""


def test_crossref_fallback_and_missing_doi_are_labelled(cal, monkeypatch):
    info = {"summary": "S", "speaker": "X", "affiliation": "", "speaker_note": ""}
    assert "DOI (Crossref match): https://doi.org/10.1/x" in sa.build_description(info, ["10.1/x"], "crossref", "L")
    assert "DOI: none found in the email" in sa.build_description(info, [], "none", "L")


def test_setup_errors_when_nothing_configured(cal, monkeypatch):
    with pytest.raises(sa.SeminarSetupError, match="no email account"):
        asyncio.run(sa.run("me", ""))
    with pytest.raises(sa.SeminarSetupError, match="no recurring 'Seminar'"):
        asyncio.run(sa.run("nobody", ""))
