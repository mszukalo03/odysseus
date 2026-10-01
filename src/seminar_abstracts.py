"""Fill a recurring seminar's weekly occurrence from its abstract email.

Seminar announcements (abstract + speaker info) are forwarded to a mailbox
connected in Odysseus. For each new one this task:

  1. summarises it with the utility (or default) model: date, speaker,
     title, a short summary;
  2. collects DOIs from the email, falling back to a Crossref title match;
  3. detaches that week's occurrence from the recurring seminar event (an
     exdate on the series) and replaces it with a one-off event carrying the
     summary, the DOI link(s) and a link to the full email in Odysseus;
  4. sends a notification through the configured reminder channel.

Config is JSON in the scheduled task's prompt (every key optional):
  {"event": "Seminar", "calendar": "MSc", "account_id": "...",
   "subject_keywords": ["seminar"], "days_back": 21, "crossref": true}

Processed messages are tracked in data/seminar_abstracts_<owner>.json.
"""

import asyncio
import difflib
import email
import json
import logging
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote, unquote

from src.constants import DATA_DIR
from src.recurring_notes import local_tz

logger = logging.getLogger(__name__)

ORIGIN = "seminar_abstracts"
DEFAULTS = {
    "event": "Seminar",
    "calendar": "",
    "account_id": "",
    "subject_keywords": ["seminar", "colloquium", "abstract"],
    "days_back": 21,
    "max_emails": 5,
    "crossref": True,
}

_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>]+)", re.IGNORECASE)


class SeminarSetupError(Exception):
    """Nothing to do until the user finishes setup (no mailbox/model/event)."""


def load_config(prompt: str) -> dict:
    cfg = dict(DEFAULTS)
    try:
        data = json.loads(prompt or "{}")
        if isinstance(data, dict):
            cfg.update({k: v for k, v in data.items() if v not in (None, "")})
    except ValueError:
        pass
    if isinstance(cfg["subject_keywords"], str):
        cfg["subject_keywords"] = [cfg["subject_keywords"]]
    cfg["subject_keywords"] = [k.lower() for k in cfg["subject_keywords"] if k]
    return cfg


# ── Parsing ──────────────────────────────────────────────────────────────

def extract_dois(*texts: str) -> list[str]:
    found = []
    for text in texts:
        for m in _DOI_RE.finditer(unquote(text or "")):
            doi = m.group(1).rstrip(".,;:)]}>'\"")
            if doi.lower() not in (d.lower() for d in found):
                found.append(doi)
    return found


def message_parts(msg) -> tuple[str, str]:
    """(plain text, raw html) of a message; html is kept for DOI hrefs."""
    from routes.email_helpers import _extract_html, _extract_text
    try:
        html = _extract_html(msg) or ""
    except Exception:
        html = ""
    return _extract_text(msg) or "", html


def _parse_llm_json(raw: str) -> dict:
    from src.text_helpers import strip_think
    text = strip_think(raw or "")
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("model reply had no JSON object")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("model reply was not a JSON object")
    return data


_SYSTEM_PROMPT = (
    "You read forwarded academic seminar announcements. Reply with one JSON "
    "object and nothing else, with these keys:\n"
    '  "seminar_date": the date the talk takes place as "YYYY-MM-DD", or null if not stated\n'
    '  "speaker": the speaker\'s full name\n'
    '  "affiliation": the speaker\'s institution, or null\n'
    '  "title": the talk title\n'
    '  "summary": a plain-language summary of the abstract in at most 3 sentences / 70 words\n'
    '  "speaker_note": one short line about the speaker (role, field), at most 25 words, or null\n'
    "Ignore forwarding headers, signatures and mailing-list footers."
)


async def summarize(subject: str, text: str, owner: str) -> dict:
    from src.endpoint_resolver import resolve_endpoint
    from src.llm_core import llm_call_async

    url, model, headers = resolve_endpoint("utility", owner=owner or None)
    if not (url and model):
        url, model, headers = resolve_endpoint("default", owner=owner or None)
    if not (url and model):
        raise SeminarSetupError("no chat model configured (set a default or utility model)")
    raw = await llm_call_async(
        url=url, model=model, headers=headers,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"Subject: {subject}\n\n{text[:12000]}"},
        ],
        temperature=0.1, max_tokens=700, timeout=180, workload="background",
    )
    info = _parse_llm_json(raw)
    for key in ("speaker", "affiliation", "title", "summary", "speaker_note", "seminar_date"):
        val = info.get(key)
        info[key] = str(val).strip() if val not in (None, "", "null") else ""
    if not info["title"] and not info["summary"]:
        raise ValueError("model reply had no title or summary")
    return info


async def crossref_doi(title: str, speaker: str) -> Optional[str]:
    """Best Crossref match for the talk title by this speaker, if close enough."""
    import httpx
    if not title:
        return None
    params = {"query.bibliographic": title, "rows": "5", "select": "DOI,title"}
    if speaker:
        params["query.author"] = speaker
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get("https://api.crossref.org/works", params=params,
                                 headers={"User-Agent": "Odysseus seminar-abstracts"})
            r.raise_for_status()
            items = r.json().get("message", {}).get("items", [])
    except Exception as e:
        logger.info("seminar_abstracts: Crossref lookup failed: %s", e)
        return None
    want = title.lower().strip()
    for item in items:
        got = " ".join(item.get("title") or []).lower().strip()
        if got and difflib.SequenceMatcher(None, want, got).ratio() >= 0.8:
            return item.get("DOI")
    return None


# ── Calendar ─────────────────────────────────────────────────────────────

def find_series(db, owner: str, cfg: dict):
    from core.database import CalendarCal, CalendarEvent
    q = (db.query(CalendarEvent)
         .join(CalendarCal, CalendarCal.id == CalendarEvent.calendar_id)
         .filter(CalendarCal.owner == owner, CalendarEvent.rrule != "",
                 CalendarEvent.rrule.isnot(None)))
    if cfg.get("calendar"):
        q = q.filter(CalendarCal.name.ilike(cfg["calendar"]))
    want = cfg["event"].strip().lower()
    for ev in q.order_by(CalendarEvent.created_at).all():
        if (ev.summary or "").strip().lower() == want:
            return ev
    return None


def _rule(series):
    from dateutil.rrule import rrulestr
    rrule_str = re.sub(r"(UNTIL=\d{8}(?:T\d{6})?)Z", r"\1", series.rrule, flags=re.IGNORECASE)
    return rrulestr(rrule_str, dtstart=series.dtstart)


def _local_date(series, occ: datetime, tz) -> date:
    if series.all_day or not series.is_utc:
        return occ.date()
    return occ.replace(tzinfo=timezone.utc).astimezone(tz).date()


def pick_occurrence(series, seminar_date: str, received: datetime, tz=None) -> Optional[datetime]:
    """Stored (naive) start of the occurrence this email is about.

    Uses the date stated in the email when there is one (and only accepts an
    occurrence on that day); otherwise the first occurrence after the email
    arrived."""
    tz = tz or local_tz()
    rule = _rule(series)
    if seminar_date:
        try:
            want = date.fromisoformat(seminar_date[:10])
        except ValueError:
            want = None
        if want:
            lo = datetime.combine(want - timedelta(days=2), datetime.min.time())
            hi = datetime.combine(want + timedelta(days=2), datetime.min.time())
            for occ in rule.between(lo, hi, inc=True):
                if _local_date(series, occ, tz) == want:
                    return occ
            return None
    after = received.astimezone(timezone.utc).replace(tzinfo=None)
    if not series.is_utc and not series.all_day:
        after = received.astimezone(tz).replace(tzinfo=None)
    return rule.after(after)


def build_description(info: dict, dois: list[str], doi_source: str, email_link: str) -> str:
    lines = [info["summary"], ""]
    speaker = info["speaker"] + (f" ({info['affiliation']})" if info["affiliation"] else "")
    if speaker.strip():
        lines.append(f"Speaker: {speaker}")
    if info["speaker_note"]:
        lines.append(info["speaker_note"])
    lines.append("")
    label = "DOI" if doi_source == "email" else "DOI (Crossref match)"
    if dois:
        lines += [f"{label}: https://doi.org/{d}" for d in dois]
    else:
        lines.append("DOI: none found in the email")
    lines.append(f"Full email: {email_link}")
    return "\n".join(lines).strip()


def apply_to_calendar(db, series, occ_start: datetime, summary: str, description: str):
    """Detach ``occ_start`` from the series and put a one-off event in its place.
    Re-running for the same occurrence updates the one-off. Returns (event, created)."""
    from core.database import CalendarCal, CalendarEvent

    existing = (db.query(CalendarEvent)
                .filter(CalendarEvent.calendar_id == series.calendar_id,
                        CalendarEvent.origin == ORIGIN,
                        CalendarEvent.dtstart == occ_start)
                .first())
    caldav = (db.query(CalendarCal.source).filter(CalendarCal.id == series.calendar_id)
              .scalar()) == "caldav"
    key = occ_start.strftime("%Y-%m-%d" if series.all_day else "%Y-%m-%dT%H:%M")
    try:
        exdates = json.loads(series.recurrence_exdates or "[]")
    except ValueError:
        exdates = []
    if key not in exdates:
        series.recurrence_exdates = json.dumps(sorted(exdates + [key]))
        if caldav:
            series.caldav_sync_pending = series.caldav_sync_pending or "update"

    if existing:
        existing.summary, existing.description = summary, description
        if caldav:
            existing.caldav_sync_pending = existing.caldav_sync_pending or "update"
        db.commit()
        return existing, False

    ev = CalendarEvent(
        uid=str(uuid.uuid4()), calendar_id=series.calendar_id,
        summary=summary, description=description, location=series.location or "",
        dtstart=occ_start, dtend=occ_start + (series.dtend - series.dtstart),
        all_day=series.all_day, is_utc=series.is_utc, rrule="",
        color=series.color, status="confirmed", importance=series.importance or "normal",
        event_type=series.event_type, origin=ORIGIN,
        caldav_sync_pending="create" if caldav else None,
    )
    db.add(ev)
    db.commit()
    return ev, True


# ── Mailbox ──────────────────────────────────────────────────────────────

def owner_accounts(owner: str, account_id: str = "") -> list[str]:
    from core.database import EmailAccount, SessionLocal
    db = SessionLocal()
    try:
        q = db.query(EmailAccount.id).filter(EmailAccount.enabled == True)  # noqa: E712
        q = q.filter(EmailAccount.owner == owner)
        if account_id:
            q = q.filter(EmailAccount.id == account_id)
        return [row[0] for row in q.order_by(EmailAccount.created_at).all()]
    finally:
        db.close()


def fetch_candidates(account_id: str, owner: str, keywords: list[str],
                     days_back: int, skip: set) -> list[tuple[str, str, object]]:
    """Recent INBOX messages whose subject contains a keyword, newest first,
    as (key, uid, message) tuples. Read-only: nothing is marked seen."""
    from routes.email_helpers import _decode_header, _imap

    since = (date.today() - timedelta(days=int(days_back))).strftime("%d-%b-%Y")
    out = []
    with _imap(account_id, owner=owner) as conn:
        conn.select("INBOX", readonly=True)
        typ, data = conn.uid("SEARCH", None, "SINCE", since)
        uids = (data[0] or b"").split()[-300:] if typ == "OK" else []
        if not uids:
            return out
        typ, data = conn.uid("FETCH", b",".join(uids),
                             "(BODY.PEEK[HEADER.FIELDS (SUBJECT MESSAGE-ID)])")
        matches = []
        for part in data or []:
            if not isinstance(part, tuple):
                continue
            m = re.search(rb"UID (\d+)", part[0])
            if not m:
                continue
            hdr = email.message_from_bytes(part[1])
            subject = _decode_header(hdr.get("Subject", "")) or ""
            if not any(k in subject.lower() for k in keywords):
                continue
            uid = m.group(1).decode()
            key = (hdr.get("Message-ID") or "").strip() or f"{account_id}:{uid}"
            if key not in skip:
                matches.append((int(uid), key))
        for uid, key in sorted(matches, reverse=True):
            typ, data = conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
            raw = next((p[1] for p in data or [] if isinstance(p, tuple)), None)
            if raw:
                out.append((key, str(uid), email.message_from_bytes(raw)))
    return out


# ── Task entry point ─────────────────────────────────────────────────────

def _state_path(owner: str) -> Path:
    slug = "".join(c if (c.isalnum() or c in "-_.@") else "_" for c in (owner or "default"))
    return Path(DATA_DIR) / f"seminar_abstracts_{slug}.json"


def _load_state(owner: str) -> dict:
    try:
        return json.loads(_state_path(owner).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _save_state(owner: str, state: dict) -> None:
    from core.atomic_io import atomic_write_json
    atomic_write_json(str(_state_path(owner)), state, indent=2)


def _email_link(uid: str) -> str:
    from src.settings import load_settings
    base = (load_settings().get("app_public_url") or "").strip().rstrip("/")
    return f"{base}/#email={quote('INBOX', safe='')}:{uid}"


async def process_message(owner: str, cfg: dict, series_id: str, uid: str, msg) -> dict:
    """Summarise one email and apply it. Returns a state record."""
    from core.database import CalendarEvent, SessionLocal
    from routes.email_helpers import _decode_header

    subject = _decode_header(msg.get("Subject", "")) or ""
    text, html = message_parts(msg)
    info = await summarize(subject, text or html, owner)

    dois, source = extract_dois(text, html), "email"
    if not dois and cfg.get("crossref"):
        doi = await crossref_doi(info["title"], info["speaker"])
        dois, source = ([doi], "crossref") if doi else ([], "none")

    try:
        received = parsedate_to_datetime(msg.get("Date"))
        if received.tzinfo is None:
            received = received.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        received = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        series = db.query(CalendarEvent).filter(CalendarEvent.uid == series_id).first()
        occ = pick_occurrence(series, info["seminar_date"], received)
        if occ is None:
            return {"status": "skipped", "subject": subject,
                    "reason": f"no '{series.summary}' occurrence on {info['seminar_date'] or 'a later date'}"}
        title = info["title"] or subject
        summary = f"{series.summary}: {title}" + (f" ({info['speaker']})" if info["speaker"] else "")
        description = build_description(info, dois, source, _email_link(uid))
        ev, created = apply_to_calendar(db, series, occ, summary, description)
        return {"status": "created" if created else "updated", "subject": subject,
                "event_uid": ev.uid, "occurrence": occ.isoformat(), "title": title,
                "summary": info["summary"], "local_date": str(_local_date(series, occ, local_tz()))}
    finally:
        db.close()


async def run(owner: str, prompt: str = "") -> str:
    """One pass. Raises SeminarSetupError when there's nothing to do yet."""
    from core.database import SessionLocal
    from routes.note_routes import dispatch_reminder

    cfg = load_config(prompt)
    db = SessionLocal()
    try:
        series = find_series(db, owner, cfg)
        series_id = series.uid if series else None
    finally:
        db.close()
    if not series_id:
        raise SeminarSetupError(f"no recurring '{cfg['event']}' event in {owner}'s calendars")
    accounts = owner_accounts(owner, cfg.get("account_id") or "")
    if not accounts:
        raise SeminarSetupError(f"no email account connected for {owner}")

    state = _load_state(owner)
    done = []
    for account_id in accounts:
        found = await asyncio.to_thread(
            fetch_candidates, account_id, owner, cfg["subject_keywords"],
            cfg["days_back"], set(state),
        )
        for key, uid, msg in found[: int(cfg["max_emails"])]:
            record = await process_message(owner, cfg, series_id, uid, msg)
            record["at"] = datetime.now(timezone.utc).isoformat()
            state[key] = record
            _save_state(owner, state)
            done.append(record)
            if record["status"] == "skipped":
                title, body = "Seminar abstract not filed", f"{record['subject']}: {record['reason']}"
            else:
                title = f"Seminar {record['local_date']}: {record['title']}"[:200]
                body = record["summary"]
            try:
                await dispatch_reminder(title=title, note_body=body,
                                        note_id=f"seminar-{record.get('event_uid') or key}",
                                        owner=owner)
            except Exception as e:
                logger.warning("seminar_abstracts: notify failed: %s", e)

    if not done:
        return ""
    return "; ".join(
        f"{r['status']} {r.get('local_date', '')} {r.get('title') or r['subject']}".strip()
        + (f" ({r['reason']})" if r.get("reason") else "")
        for r in done
    )
