import asyncio
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Query, HTTPException, Depends, Request, BackgroundTasks
from fastapi.responses import PlainTextResponse

logger = logging.getLogger(__name__)

_OWNER_FILTER = None


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _get_owner(request: Request) -> Optional[str]:
    from src.auth_helpers import get_current_user
    return get_current_user(request)


def _youtube_transcript_text(url: Optional[str], max_retries: int = 3) -> Optional[str]:
    """Fetch a YouTube video's transcript as plain text, or None if the URL
    isn't a YouTube video or no transcript is available. feedparser leaves
    `content`/`summary` empty for YouTube RSS entries, so this is the only
    source of real text to feed the AI summary for a video article."""
    if not url:
        return None
    from services.youtube.youtube_handler import is_youtube_url, extract_youtube_id, extract_transcript_async
    if not is_youtube_url(url):
        return None
    video_id = extract_youtube_id(url)
    if not video_id:
        return None
    result = asyncio.run(extract_transcript_async(url, video_id, max_retries=max_retries))
    if result.get("success"):
        return result.get("transcript") or None
    return None


def setup_feed_routes():
    router = APIRouter(prefix="/api/feeds", tags=["feeds"])

    # ─── Feed Groups ───────────────────────────────────────────────

    @router.get("/groups")
    def list_groups(request: Request):
        owner = _get_owner(request)
        from core.database import SessionLocal, FeedGroup
        db = SessionLocal()
        try:
            q = db.query(FeedGroup)
            if owner is not None:
                q = q.filter(FeedGroup.owner == owner)
            groups = [{"id": g.id, "name": g.name, "parent_id": g.parent_id} for g in q.order_by(FeedGroup.name).all()]
            return {"groups": groups}
        finally:
            db.close()

    @router.post("/groups")
    def create_group(data: dict, request: Request):
        owner = _get_owner(request)
        from core.database import SessionLocal, FeedGroup
        db = SessionLocal()
        try:
            row = FeedGroup(
                id=uuid.uuid4().hex,
                owner=owner,
                name=data.get("name", "New Group"),
                parent_id=data.get("parent_id"),
            )
            db.add(row)
            db.commit()
            return {"ok": True, "id": row.id}
        finally:
            db.close()

    @router.put("/groups/{group_id}")
    def update_group(group_id: str, data: dict, request: Request):
        owner = _get_owner(request)
        from core.database import SessionLocal, FeedGroup
        db = SessionLocal()
        try:
            row = db.query(FeedGroup).filter(FeedGroup.id == group_id).first()
            if not row or (owner is not None and row.owner != owner):
                return {"ok": False, "error": "Group not found"}
            if "name" in data:
                row.name = data["name"]
            if "parent_id" in data:
                row.parent_id = data["parent_id"] or None
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    @router.delete("/groups/{group_id}")
    def delete_group(group_id: str, request: Request):
        owner = _get_owner(request)
        from core.database import SessionLocal, FeedGroup, Feed
        db = SessionLocal()
        try:
            row = db.query(FeedGroup).filter(FeedGroup.id == group_id).first()
            if not row or (owner is not None and row.owner != owner):
                return {"ok": False, "error": "Group not found"}
            # Reparent children to the deleted group's parent
            db.query(FeedGroup).filter(FeedGroup.parent_id == group_id).update(
                {"parent_id": row.parent_id}
            )
            db.query(Feed).filter(Feed.group_id == group_id).update({"group_id": None})
            db.delete(row)
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    @router.post("/groups/{group_id}/summarize")
    def summarize_group(group_id: str, request: Request):
        owner = _get_owner(request)
        from core.database import SessionLocal, Article, Feed, FeedGroup
        db = SessionLocal()
        try:
            group = db.query(FeedGroup).filter(FeedGroup.id == group_id).first()
            if not group or (owner is not None and group.owner != owner):
                return {"ok": False, "error": "Group not found"}
            def _collect_descendant_ids(parent_id):
                ids = []
                for g in db.query(FeedGroup).filter(FeedGroup.parent_id == parent_id).all():
                    ids.append(g.id)
                    ids.extend(_collect_descendant_ids(g.id))
                return ids
            descendant_ids = _collect_descendant_ids(group_id)
            all_ids = [group_id] + descendant_ids
            feeds_q = db.query(Feed.id).filter(Feed.group_id.in_(all_ids))
            feed_ids = [r[0] for r in feeds_q.all()]
            if not feed_ids:
                return {"ok": True, "summary": "No articles in this group."}
            articles = db.query(Article).filter(
                Article.feed_id.in_(feed_ids),
                Article.is_read == False,
            ).order_by(Article.published_at.desc().nullslast()).limit(10).all()
            if not articles:
                articles = db.query(Article).filter(
                    Article.feed_id.in_(feed_ids),
                ).order_by(Article.published_at.desc().nullslast()).limit(8).all()
            # Fetch transcripts for any YouTube entries with empty content
            # (feedparser never populates content/summary for video entries).
            # Concurrent + a single retry keeps this bounded for a group of
            # several videos, given the 45s REQUEST_HARD_TIMEOUT.
            empty_youtube = [a for a in articles if not (a.content or "").strip() and a.url]
            if empty_youtube:
                from services.youtube.youtube_handler import is_youtube_url, extract_youtube_id, extract_transcript_async

                async def _fetch_all():
                    async def _one(a):
                        if not is_youtube_url(a.url):
                            return a, None
                        video_id = extract_youtube_id(a.url)
                        if not video_id:
                            return a, None
                        result = await extract_transcript_async(a.url, video_id, max_retries=1)
                        return a, (result.get("transcript") if result.get("success") else None)
                    return await asyncio.gather(*[_one(a) for a in empty_youtube])

                for a, transcript in asyncio.run(_fetch_all()):
                    if transcript:
                        a.content = transcript
                db.commit()

            text_parts = []
            for a in articles:
                title = a.title or "Untitled"
                content = (a.content or "")[:400]
                text_parts.append(f"## {title}\n\n{content}")
            combined = "\n\n---\n\n".join(text_parts)
            if not combined.strip():
                return {"ok": True, "summary": "No content available to summarize."}
            from src.llm_core import query_llm
            prompt = (
                f"Summarize the following {len(articles)} articles as a concise bullet-point digest. "
                f"Group related articles under common themes. Keep each bullet to 1-2 sentences.\n\n{combined[:6000]}"
            )
            # /api/feeds/groups is exempt from REQUEST_HARD_TIMEOUT (see
            # app.py) for the same reason /api/feeds/articles is — slow
            # models can legitimately take longer than 45s. Match the same
            # 180s ceiling used for the single-article summarizer.
            summary = query_llm(
                prompt,
                # See the single-article summarizer for why this instruction
                # is needed: reasoning-style models not covered by
                # _THINKING_MODEL_PATTERNS emit their chain-of-thought as
                # plain prose with no separable field/tag to strip.
                system_prompt=(
                    "You are a helpful assistant that writes concise multi-article digests. "
                    "Respond with only the final digest — no reasoning, analysis, or "
                    "commentary about the task itself."
                ),
                max_tokens=1200,
                timeout=180,
            )
            return {"ok": True, "summary": summary}
        except Exception as e:
            logger.warning("Group summarize failed: %s", e)
            return {"ok": False, "error": f"Group summarization failed: {e}"}
        finally:
            db.close()

    # ─── Feeds ─────────────────────────────────────────────────────

    @router.get("")
    def list_feeds(request: Request):
        owner = _get_owner(request)
        from core.database import SessionLocal, Feed
        db = SessionLocal()
        try:
            q = db.query(Feed)
            if owner is not None:
                q = q.filter(Feed.owner == owner)
            feeds = []
            for f in q.order_by(Feed.sort_order, Feed.title).all():
                unread = 0
                try:
                    from core.database import Article
                    unread = db.query(Article).filter(
                        Article.feed_id == f.id, Article.is_read == False
                    ).count()
                except Exception:
                    pass
                feeds.append({
                    "id": f.id,
                    "group_id": f.group_id,
                    "title": f.title,
                    "site_url": f.site_url,
                    "feed_url": f.feed_url,
                    "icon": f.icon,
                    "fetch_interval": f.fetch_interval,
                    "sort_order": f.sort_order,
                    "last_fetched": f.last_fetched.isoformat() if f.last_fetched else None,
                    "error_count": f.error_count,
                    "last_error": f.last_error,
                    "enabled": f.enabled,
                    "unread": unread,
                    "created_at": f.created_at.isoformat() if f.created_at else None,
                })
            return {"feeds": feeds}
        finally:
            db.close()

    @router.post("")
    def create_feed(data: dict, request: Request):
        owner = _get_owner(request)
        from core.database import SessionLocal, Feed
        db = SessionLocal()
        try:
            feed_url = (data.get("feed_url") or "").strip()
            if not feed_url:
                return {"ok": False, "error": "feed_url required"}
            from services.feed.youtube import resolve_youtube_feed
            resolved = resolve_youtube_feed(feed_url)
            is_youtube = False
            if resolved:
                feed_url = resolved
                is_youtube = True
            icon = data.get("icon")
            if not icon and is_youtube:
                icon = "https://www.youtube.com/favicon.ico"
            row = Feed(
                id=uuid.uuid4().hex,
                owner=owner,
                group_id=data.get("group_id"),
                title=data.get("title", ""),
                site_url=data.get("site_url", ""),
                feed_url=feed_url,
                icon=icon,
                fetch_interval=int(data.get("fetch_interval", 60)),
                enabled=True,
            )
            db.add(row)
            db.commit()
            return {"ok": True, "id": row.id}
        finally:
            db.close()

    @router.put("/{feed_id}")
    def update_feed(feed_id: str, data: dict, request: Request):
        owner = _get_owner(request)
        from core.database import SessionLocal, Feed
        db = SessionLocal()
        try:
            row = db.query(Feed).filter(Feed.id == feed_id).first()
            if not row or (owner is not None and row.owner != owner):
                return {"ok": False, "error": "Feed not found"}
            for key in ("title", "feed_url", "site_url", "group_id", "icon", "fetch_interval", "enabled", "sort_order"):
                if key in data:
                    setattr(row, key, data[key])
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    @router.delete("/{feed_id}")
    def delete_feed(feed_id: str, request: Request):
        owner = _get_owner(request)
        from core.database import SessionLocal, Feed, Article
        db = SessionLocal()
        try:
            row = db.query(Feed).filter(Feed.id == feed_id).first()
            if not row or (owner is not None and row.owner != owner):
                return {"ok": False, "error": "Feed not found"}
            db.query(Article).filter(Article.feed_id == feed_id).delete()
            db.delete(row)
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    # ─── Feed Discovery ────────────────────────────────────────────

    @router.post("/discover")
    def discover_feed(data: dict, request: Request):
        url = (data.get("url") or "").strip()
        if not url:
            return {"ok": False, "error": "url required"}
        from services.feed.discovery import discover_feeds
        feeds = discover_feeds(url)
        return {"feeds": feeds}

    # ─── Force Refresh ─────────────────────────────────────────────

    @router.post("/{feed_id}/refresh")
    def refresh_feed(feed_id: str, request: Request):
        owner = _get_owner(request)
        from core.database import SessionLocal, Feed, Article
        from services.feed.fetcher import fetch_feed
        db = SessionLocal()
        try:
            row = db.query(Feed).filter(Feed.id == feed_id).first()
            if not row or (owner is not None and row.owner != owner):
                return {"ok": False, "error": "Feed not found"}
            result = fetch_feed(row.feed_url)
            if result is None:
                row.error_count = (row.error_count or 0) + 1
                row.last_error = "Fetch failed"
                db.commit()
                return {"ok": False, "error": "Failed to fetch feed"}
            row.title = result["title"] or row.title
            if result["site_url"]:
                row.site_url = result["site_url"]
            if result["icon"]:
                row.icon = result["icon"]
            row.error_count = 0
            row.last_error = None
            row.last_fetched = _utcnow()
            existing_guids = set()
            for a in db.query(Article.guid).filter(Article.feed_id == feed_id).all():
                if a.guid:
                    existing_guids.add(a.guid)
            new_count = 0
            for entry in result["entries"]:
                if entry["guid"] in existing_guids:
                    continue
                published = None
                if entry.get("published_at"):
                    try:
                        published = datetime.fromisoformat(entry["published_at"])
                    except Exception:
                        pass
                art = Article(
                    id=uuid.uuid4().hex,
                    feed_id=feed_id,
                    owner=owner,
                    guid=entry["guid"],
                    title=entry["title"],
                    url=entry["url"],
                    author=entry.get("author"),
                    content=entry.get("content"),
                    image=entry.get("image"),
                    published_at=published,
                    fetched_at=_utcnow(),
                )
                db.add(art)
                new_count += 1
            db.commit()
            return {"ok": True, "new_articles": new_count, "title": row.title, "icon": row.icon}
        finally:
            db.close()

    @router.post("/refresh-all")
    def refresh_all_feeds(request: Request, background_tasks: BackgroundTasks):
        owner = _get_owner(request)
        from core.database import SessionLocal, Feed
        db = SessionLocal()
        try:
            q = db.query(Feed).filter(Feed.enabled == True)
            if owner is not None:
                q = q.filter(Feed.owner == owner)
            feed_ids = [f.id for f in q.all()]
            for fid in feed_ids:
                background_tasks.add_task(_refresh_single, fid, owner)
            return {"ok": True, "queued": len(feed_ids)}
        finally:
            db.close()

    # ─── Articles ──────────────────────────────────────────────────

    @router.get("/articles")
    def list_articles(
        request: Request,
        feed_id: Optional[str] = Query(None),
        group_id: Optional[str] = Query(None),
        group_ids: Optional[str] = Query(None),
        starred: Optional[bool] = Query(None),
        read: Optional[bool] = Query(None),
        search: Optional[str] = Query(None),
        limit: int = Query(50),
        offset: int = Query(0),
    ):
        owner = _get_owner(request)
        from core.database import SessionLocal, Article, Feed
        db = SessionLocal()
        try:
            q = db.query(Article)
            if owner is not None:
                q = q.filter(Article.owner == owner)
            if feed_id:
                q = q.filter(Article.feed_id == feed_id)
            if group_ids:
                gids = [g.strip() for g in group_ids.split(",") if g.strip()]
                sub = db.query(Feed.id).filter(Feed.group_id.in_(gids)).subquery()
                q = q.filter(Article.feed_id.in_(sub))
            elif group_id:
                sub = db.query(Feed.id).filter(Feed.group_id == group_id).subquery()
                q = q.filter(Article.feed_id.in_(sub))
            if starred is True:
                q = q.filter(Article.is_starred == True)
            if read is True:
                q = q.filter(Article.is_read == True)
            elif read is False:
                q = q.filter(Article.is_read == False)
            if search:
                q = q.filter(Article.title.ilike(f"%{search}%") | Article.content.ilike(f"%{search}%"))
            total = q.count()
            articles = q.order_by(Article.published_at.desc().nullslast()).offset(offset).limit(limit).all()
            feed_ids = {a.feed_id for a in articles}
            feed_map = {}
            for f in db.query(Feed).filter(Feed.id.in_(feed_ids)).all():
                feed_map[f.id] = {"title": f.title, "icon": f.icon, "site_url": f.site_url}
            result = []
            for a in articles:
                result.append({
                    "id": a.id,
                    "feed_id": a.feed_id,
                    "feed": feed_map.get(a.feed_id, {}),
                    "guid": a.guid,
                    "title": a.title,
                    "url": a.url,
                    "author": a.author,
                    "content": a.content,
                    "summary": a.summary,
                    "image": a.image,
                    "published_at": a.published_at.isoformat() if a.published_at else None,
                    "fetched_at": a.fetched_at.isoformat() if a.fetched_at else None,
                    "is_read": a.is_read,
                    "is_starred": a.is_starred,
                    "reading_time": a.reading_time,
                })
            return {"articles": result, "total": total}
        finally:
            db.close()

    @router.put("/articles/{article_id}/read")
    def mark_read(article_id: str, data: dict, request: Request):
        owner = _get_owner(request)
        from core.database import SessionLocal, Article
        db = SessionLocal()
        try:
            row = db.query(Article).filter(Article.id == article_id).first()
            if not row or (owner is not None and row.owner != owner):
                return {"ok": False, "error": "Article not found"}
            row.is_read = data.get("is_read", True)
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    @router.put("/articles/{article_id}/star")
    def toggle_star(article_id: str, data: dict, request: Request):
        owner = _get_owner(request)
        from core.database import SessionLocal, Article
        db = SessionLocal()
        try:
            row = db.query(Article).filter(Article.id == article_id).first()
            if not row or (owner is not None and row.owner != owner):
                return {"ok": False, "error": "Article not found"}
            row.is_starred = data.get("is_starred", True)
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    @router.post("/articles/mark-all-read")
    def mark_all_read(data: dict, request: Request):
        owner = _get_owner(request)
        feed_id = data.get("feed_id")
        from core.database import SessionLocal, Article
        db = SessionLocal()
        try:
            q = db.query(Article).filter(Article.is_read == False)
            if owner is not None:
                q = q.filter(Article.owner == owner)
            if feed_id:
                q = q.filter(Article.feed_id == feed_id)
            q.update({"is_read": True})
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    # ─── AI Summary ────────────────────────────────────────────────

    @router.post("/articles/{article_id}/summarize")
    def summarize_article(article_id: str, request: Request):
        owner = _get_owner(request)
        from core.database import SessionLocal, Article
        db = SessionLocal()
        try:
            row = db.query(Article).filter(Article.id == article_id).first()
            if not row or (owner is not None and row.owner != owner):
                return {"ok": False, "error": "Article not found"}
            if row.summary:
                return {"ok": True, "summary": row.summary}
            text = row.content or ""
            if not text.strip():
                transcript = _youtube_transcript_text(row.url)
                if transcript:
                    text = transcript
                    row.content = text
                    db.commit()
            if not text.strip():
                return {"ok": False, "error": "No content to summarize"}
            try:
                from src.llm_core import query_llm
                # Some models (large local models, or slow-but-otherwise-fine
                # hosted ones like big-pickle) legitimately take well over the
                # app's 45s REQUEST_HARD_TIMEOUT to produce even a short reply
                # — the same reason /api/chat is exempt from that timeout
                # (it streams). /api/feeds/articles and /api/feeds/groups are
                # exempted from REQUEST_HARD_TIMEOUT the same way (see app.py),
                # so this query_llm `timeout` is now the real ceiling — set to
                # match the 180s already used for slow-model tolerance
                # elsewhere in this codebase (deep research synthesis). Input
                # is still capped at 3000 chars / 500 tokens out purely to
                # keep the summary itself short, not to dodge a timeout.
                prompt = f"Summarize the following article in 2-3 paragraphs:\n\n{text[:3000]}"
                summary = query_llm(
                    prompt,
                    # Reasoning-style models (e.g. big-pickle) aren't in this
                    # codebase's known thinking-model list (_THINKING_MODEL_PATTERNS),
                    # so the existing <think>-tag stripping never fires for them —
                    # they just emit their chain-of-thought as plain prose ahead of
                    # the actual answer. Telling them explicitly to skip it is the
                    # only lever available short of per-model output parsing.
                    system_prompt=(
                        "You are a helpful assistant that writes concise article summaries. "
                        "Respond with only the final summary text — no reasoning, analysis, "
                        "or commentary about the task itself."
                    ),
                    max_tokens=1200,
                    timeout=180,
                )
                row.summary = summary
                db.commit()
                return {"ok": True, "summary": summary}
            except Exception as e:
                logger.warning("Summarize failed: %s", e)
                return {"ok": False, "error": f"Summarization failed: {e}"}
        finally:
            db.close()

    # ─── Full Content ──────────────────────────────────────────────

    @router.post("/articles/{article_id}/full-content")
    def fetch_full_content(article_id: str, request: Request):
        owner = _get_owner(request)
        from core.database import SessionLocal, Article
        db = SessionLocal()
        try:
            row = db.query(Article).filter(Article.id == article_id).first()
            if not row or (owner is not None and row.owner != owner):
                return {"ok": False, "error": "Article not found"}
            if not row.url:
                return {"ok": False, "error": "No article URL"}
            from services.youtube.youtube_handler import is_youtube_url
            if is_youtube_url(row.url):
                # trafilatura can't extract anything useful from a YouTube
                # watch page (JS-rendered SPA) — use the transcript instead.
                transcript = _youtube_transcript_text(row.url)
                if not transcript:
                    return {"ok": False, "error": "No transcript available for this video"}
                row.content = transcript
                db.commit()
                return {"ok": True, "content": transcript}
            from services.feed.full_content import extract_content
            result = extract_content(row.url)
            if result is None:
                return {"ok": False, "error": "Could not extract content"}
            row.content = result["content"]
            db.commit()
            return {"ok": True, "content": result["content"]}
        finally:
            db.close()

    # ─── OPML ──────────────────────────────────────────────────────

    @router.post("/opml/import")
    def import_opml(data: dict, request: Request):
        owner = _get_owner(request)
        opml_text = data.get("opml", "")
        if not opml_text:
            return {"ok": False, "error": "OPML content required"}
        from services.feed.opml import parse_opml
        from services.feed.youtube import resolve_youtube_feed
        from core.database import SessionLocal, Feed, FeedGroup
        from sqlalchemy import or_
        feeds = parse_opml(opml_text)
        db = SessionLocal()
        try:
            # Resolve YouTube URLs and resolve group paths to group IDs
            group_cache = {}  # (owner, tuple(path)) -> group_id

            def _get_or_create_group(group_path):
                if not group_path:
                    return None
                key = (owner, tuple(group_path))
                if key in group_cache:
                    return group_cache[key]
                # Walk the path, creating missing groups
                parent_id = None
                for seg in group_path:
                    filters = [FeedGroup.name == seg, FeedGroup.parent_id == parent_id]
                    if owner is not None:
                        filters.append(FeedGroup.owner == owner)
                    else:
                        filters.append(FeedGroup.owner.is_(None))
                    existing = db.query(FeedGroup).filter(*filters).first()
                    if existing:
                        parent_id = existing.id
                    else:
                        g = FeedGroup(
                            id=uuid.uuid4().hex,
                            owner=owner,
                            name=seg,
                            parent_id=parent_id,
                        )
                        db.add(g)
                        db.flush()
                        parent_id = g.id
                group_cache[key] = parent_id
                return parent_id

            imported = 0
            for f in feeds:
                feed_url = f["feed_url"]
                resolved = resolve_youtube_feed(feed_url)
                if resolved:
                    feed_url = resolved

                # Check duplicate against both raw and resolved URL
                dup_urls = [feed_url]
                if resolved and resolved != f["feed_url"]:
                    dup_urls.append(f["feed_url"])
                filters = [Feed.feed_url.in_(dup_urls)]
                if owner is not None:
                    filters.append(Feed.owner == owner)
                else:
                    filters.append(Feed.owner.is_(None))
                existing = db.query(Feed).filter(*filters).first()
                if existing:
                    continue
                group_id = _get_or_create_group(f.get("group_path", []))
                row = Feed(
                    id=uuid.uuid4().hex,
                    owner=owner,
                    group_id=group_id,
                    title=f["title"],
                    feed_url=feed_url,
                    site_url=f.get("site_url", ""),
                )
                db.add(row)
                imported += 1
            db.commit()
            return {"ok": True, "imported": imported}
        finally:
            db.close()

    @router.get("/opml/export")
    def export_opml(request: Request):
        owner = _get_owner(request)
        from core.database import SessionLocal, Feed
        from services.feed.opml import generate_opml
        db = SessionLocal()
        try:
            q = db.query(Feed)
            if owner is not None:
                q = q.filter(Feed.owner == owner)
            feeds = [{"title": f.title, "feed_url": f.feed_url, "site_url": f.site_url} for f in q.all()]
            opml = generate_opml(feeds)
            return PlainTextResponse(opml, media_type="text/xml")
        finally:
            db.close()

    return router


def _refresh_single(feed_id: str, owner: Optional[str]):
    from core.database import SessionLocal, Feed, Article
    from services.feed.fetcher import fetch_feed
    db = SessionLocal()
    try:
        row = db.query(Feed).filter(Feed.id == feed_id).first()
        if not row:
            return
        result = fetch_feed(row.feed_url)
        if result is None:
            row.error_count = (row.error_count or 0) + 1
            row.last_error = "Fetch failed"
            db.commit()
            return
        row.title = result["title"] or row.title
        if result["site_url"]:
            row.site_url = result["site_url"]
        if result["icon"]:
            row.icon = result["icon"]
        row.error_count = 0
        row.last_error = None
        row.last_fetched = _utcnow()
        existing_guids = set()
        for a in db.query(Article.guid).filter(Article.feed_id == feed_id).all():
            if a.guid:
                existing_guids.add(a.guid)
        for entry in result["entries"]:
            if entry["guid"] in existing_guids:
                continue
            published = None
            if entry.get("published_at"):
                try:
                    published = datetime.fromisoformat(entry["published_at"])
                except Exception:
                    pass
            art = Article(
                id=uuid.uuid4().hex,
                feed_id=feed_id,
                owner=owner,
                guid=entry["guid"],
                title=entry["title"],
                url=entry["url"],
                author=entry.get("author"),
                content=entry.get("content"),
                image=entry.get("image"),
                published_at=published,
                fetched_at=_utcnow(),
            )
            db.add(art)
        db.commit()
    except Exception as e:
        logger.error("Background refresh failed for feed %s: %s", feed_id, e)
    finally:
        db.close()
