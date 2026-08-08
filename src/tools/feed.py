"""RSS-domain tool implementations.

Agent access to the RSS/YouTube feed reader (extensions/rss/), following
the same small-purpose-built-tools shape as src/tools/ithaca.py rather than
a generic manage_X dispatcher — RSS is a workspace extension like Ithaca,
not a legacy hardcoded-in-index.html feature like Notes/Calendar:
- list_rss_feeds: the user's feeds and groups, with unread counts.
- get_rss_articles: articles from a feed/group, optionally filtered — the
  "run" half of the list/run pairing list_rss_feeds sets up.
- summarize_rss_articles: summarize one article or digest a group's unread
  articles — an optional-parameter mode switch (article_id vs. group_id),
  mirroring query_ithaca_tile's no-id/with-id pairing rather than an action
  enum.
- mark_rss_article: mark an article read/unread and/or starred/unstarred.
  The one mutation tool in this set — Ithaca's tiles have no analogous
  write path, but "mark that read" after the agent has shown an article to
  the user is a real, minimal case here.

All four call the same module-level query helpers extensions/rss/backend.py
uses for its own HTTP routes, so there is exactly one implementation of
each query, not one for the API and a second for the agent.
"""

import logging
from typing import Dict, Optional

from src.tools._common import _parse_tool_args

logger = logging.getLogger(__name__)


async def do_list_rss_feeds(content: str, owner: Optional[str] = None) -> Dict:
    """Handle list_rss_feeds: the user's RSS/YouTube feeds and groups, with
    unread counts. Read-only, no required parameters."""
    from core.database import SessionLocal
    from extensions.rss.backend import _list_feeds, _list_groups

    try:
        db = SessionLocal()
        try:
            feeds = _list_feeds(db, owner)["feeds"]
            groups = _list_groups(db, owner)["groups"]
        finally:
            db.close()

        if not feeds:
            return {"response": "No RSS feeds configured yet.", "feeds": [], "groups": groups, "exit_code": 0}

        group_names = {g["id"]: g["name"] for g in groups}
        lines = ["RSS feeds (call get_rss_articles with a feed_id or group_id to read one):"]
        for f in feeds:
            group_note = f" [{group_names[f['group_id']]}]" if f.get("group_id") in group_names else ""
            unread_note = f" — {f['unread']} unread" if f.get("unread") else ""
            lines.append(f"- {f['id']}: {f['title'] or f['feed_url']}{group_note}{unread_note}")
        return {"response": "\n".join(lines), "feeds": feeds, "groups": groups, "exit_code": 0}
    except Exception as e:
        logger.error(f"list_rss_feeds error: {e}")
        return {"error": str(e), "exit_code": 1}


async def do_get_rss_articles(content: str, owner: Optional[str] = None) -> Dict:
    """Handle get_rss_articles: articles from a feed or group, optionally
    filtered by read/starred state or a search term."""
    from core.database import SessionLocal
    from extensions.rss.backend import _list_articles

    try:
        args = _parse_tool_args(content)
    except ValueError:
        args = {}

    try:
        limit = int(args.get("limit") or 20)
    except (TypeError, ValueError):
        limit = 20

    try:
        db = SessionLocal()
        try:
            data = _list_articles(
                db, owner,
                feed_id=args.get("feed_id"),
                group_id=args.get("group_id"),
                starred=args.get("starred"),
                read=args.get("read"),
                search=args.get("search"),
                limit=limit,
            )
        finally:
            db.close()

        articles = data["articles"]
        if not articles:
            return {"response": "No matching articles.", "articles": [], "total": data["total"], "exit_code": 0}

        lines = [f"{data['total']} matching article(s), showing {len(articles)}:"]
        for a in articles:
            flags = []
            if not a["is_read"]:
                flags.append("unread")
            if a["is_starred"]:
                flags.append("starred")
            flag_note = f" [{', '.join(flags)}]" if flags else ""
            source = (a.get("feed") or {}).get("title") or ""
            source_note = f" ({source})" if source else ""
            lines.append(f"- {a['id']}: {a['title']}{flag_note}{source_note}")
        return {"response": "\n".join(lines), "articles": articles, "total": data["total"], "exit_code": 0}
    except Exception as e:
        logger.error(f"get_rss_articles error: {e}")
        return {"error": str(e), "exit_code": 1}


async def do_summarize_rss_articles(content: str, owner: Optional[str] = None) -> Dict:
    """Handle summarize_rss_articles: summarize a single article
    (article_id) or digest a group's unread articles (group_id)."""
    from core.database import SessionLocal
    from extensions.rss.backend import _summarize_article_row, _summarize_group_articles

    try:
        args = _parse_tool_args(content)
    except ValueError:
        args = {}
    article_id = str(args.get("article_id") or "").strip()
    group_id = str(args.get("group_id") or "").strip()

    if not article_id and not group_id:
        return {"error": "Provide either article_id or group_id", "exit_code": 1}

    try:
        db = SessionLocal()
        try:
            if article_id:
                result = _summarize_article_row(db, article_id, owner)
            else:
                result = _summarize_group_articles(db, group_id, owner)
        finally:
            db.close()

        if not result.get("ok"):
            return {"error": result.get("error", "Summarization failed"), "exit_code": 1}
        return {"response": result["summary"], "exit_code": 0}
    except Exception as e:
        logger.error(f"summarize_rss_articles error: {e}")
        return {"error": str(e), "exit_code": 1}


async def do_mark_rss_article(content: str, owner: Optional[str] = None) -> Dict:
    """Handle mark_rss_article: mark an article read/unread and/or
    starred/unstarred."""
    from core.database import SessionLocal
    from extensions.rss.backend import _mark_article_read, _toggle_article_star

    try:
        args = _parse_tool_args(content)
    except ValueError:
        args = {}
    article_id = str(args.get("article_id") or "").strip()
    if not article_id:
        return {"error": "article_id is required", "exit_code": 1}
    is_read = args.get("is_read")
    is_starred = args.get("is_starred")
    if is_read is None and is_starred is None:
        return {"error": "Provide is_read or is_starred", "exit_code": 1}

    try:
        db = SessionLocal()
        try:
            result = {"ok": True}
            if is_read is not None:
                result = _mark_article_read(db, article_id, owner, is_read=bool(is_read))
            if result.get("ok") and is_starred is not None:
                result = _toggle_article_star(db, article_id, owner, is_starred=bool(is_starred))
        finally:
            db.close()

        if not result.get("ok"):
            return {"error": result.get("error", "Update failed"), "exit_code": 1}
        changes = []
        if is_read is not None:
            changes.append("read" if is_read else "unread")
        if is_starred is not None:
            changes.append("starred" if is_starred else "unstarred")
        return {"response": f"Marked article as {' and '.join(changes)}.", "exit_code": 0}
    except Exception as e:
        logger.error(f"mark_rss_article error: {e}")
        return {"error": str(e), "exit_code": 1}
