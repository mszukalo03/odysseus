"""Tests for src/tools/feed.py — the agent-facing list_rss_feeds,
get_rss_articles, summarize_rss_articles, and mark_rss_article tools. All
four call the same module-level helpers extensions/rss/backend.py uses for
its HTTP routes, so these tests monkeypatch those helpers directly rather
than a real DB session."""

import extensions.rss.backend as rss_backend
from src.tools.feed import (
    do_list_rss_feeds,
    do_get_rss_articles,
    do_summarize_rss_articles,
    do_mark_rss_article,
)


async def test_list_rss_feeds_empty_state(monkeypatch):
    monkeypatch.setattr(rss_backend, "_list_feeds", lambda db, owner: {"feeds": []})
    monkeypatch.setattr(rss_backend, "_list_groups", lambda db, owner: {"groups": []})

    result = await do_list_rss_feeds("{}", owner="alice")
    assert result["exit_code"] == 0
    assert "No RSS feeds" in result["response"]


async def test_list_rss_feeds_respects_owner(monkeypatch):
    seen_owners = []

    def fake_list_feeds(db, owner):
        seen_owners.append(owner)
        return {"feeds": [{"id": "f1", "title": "Blog", "feed_url": "https://x/feed", "group_id": None, "unread": 3}]}

    monkeypatch.setattr(rss_backend, "_list_feeds", fake_list_feeds)
    monkeypatch.setattr(rss_backend, "_list_groups", lambda db, owner: {"groups": []})

    result = await do_list_rss_feeds("{}", owner="alice")
    assert result["exit_code"] == 0
    assert seen_owners == ["alice"]
    assert "3 unread" in result["response"]


async def test_get_rss_articles_owner_scoped(monkeypatch):
    seen = {}

    def fake_list_articles(db, owner, **kwargs):
        seen["owner"] = owner
        seen["kwargs"] = kwargs
        return {"articles": [], "total": 0}

    monkeypatch.setattr(rss_backend, "_list_articles", fake_list_articles)

    result = await do_get_rss_articles('{"feed_id": "f1", "read": false}', owner="bob")
    assert result["exit_code"] == 0
    assert seen["owner"] == "bob"
    assert seen["kwargs"]["feed_id"] == "f1"
    assert seen["kwargs"]["read"] is False
    assert "No matching articles" in result["response"]


async def test_get_rss_articles_formats_results(monkeypatch):
    def fake_list_articles(db, owner, **kwargs):
        return {
            "articles": [
                {"id": "a1", "title": "Big News", "is_read": False, "is_starred": True, "feed": {"title": "Blog"}},
            ],
            "total": 1,
        }
    monkeypatch.setattr(rss_backend, "_list_articles", fake_list_articles)

    result = await do_get_rss_articles("{}", owner="bob")
    assert result["exit_code"] == 0
    assert "Big News" in result["response"]
    assert "unread" in result["response"]
    assert "starred" in result["response"]


async def test_summarize_rss_articles_requires_id():
    result = await do_summarize_rss_articles("{}", owner="alice")
    assert result["exit_code"] == 1
    assert "article_id" in result["error"] or "group_id" in result["error"]


async def test_summarize_rss_articles_by_article_id(monkeypatch):
    seen = {}

    def fake_summarize_article(db, article_id, owner):
        seen["article_id"] = article_id
        seen["owner"] = owner
        return {"ok": True, "summary": "A concise summary."}

    monkeypatch.setattr(rss_backend, "_summarize_article_row", fake_summarize_article)

    result = await do_summarize_rss_articles('{"article_id": "a1"}', owner="alice")
    assert result["exit_code"] == 0
    assert result["response"] == "A concise summary."
    assert seen == {"article_id": "a1", "owner": "alice"}


async def test_summarize_rss_articles_failure_surfaces_error(monkeypatch):
    monkeypatch.setattr(
        rss_backend, "_summarize_group_articles",
        lambda db, group_id, owner: {"ok": False, "error": "Group not found"},
    )

    result = await do_summarize_rss_articles('{"group_id": "g1"}', owner="alice")
    assert result["exit_code"] == 1
    assert result["error"] == "Group not found"


async def test_mark_rss_article_requires_article_id():
    result = await do_mark_rss_article('{"is_read": true}', owner="alice")
    assert result["exit_code"] == 1
    assert "article_id" in result["error"]


async def test_mark_rss_article_requires_a_field():
    result = await do_mark_rss_article('{"article_id": "a1"}', owner="alice")
    assert result["exit_code"] == 1


async def test_mark_rss_article_owner_scoped(monkeypatch):
    seen = {}

    def fake_mark_read(db, article_id, owner, is_read=True):
        seen["mark_read"] = (article_id, owner, is_read)
        return {"ok": True}

    monkeypatch.setattr(rss_backend, "_mark_article_read", fake_mark_read)

    result = await do_mark_rss_article('{"article_id": "a1", "is_read": true}', owner="carol")
    assert result["exit_code"] == 0
    assert seen["mark_read"] == ("a1", "carol", True)
    assert "read" in result["response"]


async def test_mark_rss_article_not_found(monkeypatch):
    monkeypatch.setattr(
        rss_backend, "_mark_article_read",
        lambda db, article_id, owner, is_read=True: {"ok": False, "error": "Article not found"},
    )

    result = await do_mark_rss_article('{"article_id": "nope", "is_read": true}', owner="carol")
    assert result["exit_code"] == 1
    assert result["error"] == "Article not found"


def test_tools_registered_in_dispatcher():
    from src.agent_tools import TOOL_TAGS
    assert "list_rss_feeds" in TOOL_TAGS
    assert "get_rss_articles" in TOOL_TAGS
    assert "summarize_rss_articles" in TOOL_TAGS
    assert "mark_rss_article" in TOOL_TAGS


def test_tools_have_function_schemas():
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    names = [s["function"]["name"] for s in FUNCTION_TOOL_SCHEMAS]
    for name in ("list_rss_feeds", "get_rss_articles", "summarize_rss_articles", "mark_rss_article"):
        assert names.count(name) == 1


async def test_execute_tool_block_dispatches_by_name(monkeypatch):
    monkeypatch.setattr(rss_backend, "_list_feeds", lambda db, owner: {"feeds": []})
    monkeypatch.setattr(rss_backend, "_list_groups", lambda db, owner: {"groups": []})

    from src.agent_tools import ToolBlock, execute_tool_block
    desc, result = await execute_tool_block(ToolBlock("list_rss_feeds", "{}"))
    assert desc == "list_rss_feeds"
    assert result["exit_code"] == 0
