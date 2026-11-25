"""Bearer API tokens reach the research routes only with research scopes,
and then act as the token's owner (not the sandboxed "api" pseudo-user)."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from routes.research_routes import setup_research_routes


@pytest.fixture(autouse=True)
def _redirect_research_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "routes.research_routes.DEEP_RESEARCH_DIR",
        str(tmp_path / "data" / "deep_research"),
    )


def _token_request(scopes, owner="alice"):
    state = SimpleNamespace(
        current_user="api",
        api_token=True,
        api_token_owner=owner,
        api_token_scopes=list(scopes),
    )
    return SimpleNamespace(state=state, headers={}, app=SimpleNamespace(state=SimpleNamespace()))


def _route(router, path, method):
    for route in router.routes:
        if getattr(route, "path", "") == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"{method} {path} route not registered")


def _handler():
    handler = MagicMock()
    handler._active_tasks = {}
    return handler


def _write(data_dir, sid, **data):
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / f"{sid}.json").write_text(json.dumps(data), encoding="utf-8")


def test_library_with_read_scope_lists_owner_reports(tmp_path):
    data_dir = tmp_path / "data" / "deep_research"
    _write(data_dir, "alice-1", owner="alice", query="A", completed_at=1)
    _write(data_dir, "api-1", owner="api", query="silo", completed_at=2)
    router = setup_research_routes(_handler())
    target = _route(router, "/api/research/library", "GET")
    out = asyncio.run(target(request=_token_request(["research:read"]), search=None,
                             sort="recent", limit=50, archived=False))
    assert [i["id"] for i in out["research"]] == ["alice-1"]


def test_library_without_scope_is_refused():
    router = setup_research_routes(_handler())
    target = _route(router, "/api/research/library", "GET")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(target(request=_token_request(["feeds:read"]), search=None,
                           sort="recent", limit=50, archived=False))
    assert exc.value.status_code == 403


def test_delete_needs_launch_scope(tmp_path):
    data_dir = tmp_path / "data" / "deep_research"
    _write(data_dir, "alice-del", owner="alice", query="A")
    router = setup_research_routes(_handler())
    target = _route(router, "/api/research/{session_id}", "DELETE")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(target(session_id="alice-del", request=_token_request(["research:read"])))
    assert exc.value.status_code == 403
    out = asyncio.run(target(session_id="alice-del",
                             request=_token_request(["research:read", "research:launch"])))
    assert out == {"deleted": True}


def test_start_with_read_scope_only_is_refused():
    router = setup_research_routes(_handler())
    target = _route(router, "/api/research/start", "POST")
    body_cls = target.__annotations__["body"]
    with pytest.raises(HTTPException) as exc:
        asyncio.run(target(body=body_cls(query="q"), request=_token_request(["research:read"])))
    assert exc.value.status_code == 403


def test_start_with_launch_scope_runs_as_owner(monkeypatch):
    handler = _handler()
    monkeypatch.setattr(
        "routes.research_routes.resolve_endpoint",
        lambda role, owner=None, **kw: ("http://llm/v1/chat/completions", "m", {}) if owner == "alice" else (None, None, None),
    )
    router = setup_research_routes(handler)
    target = _route(router, "/api/research/start", "POST")
    body_cls = target.__annotations__["body"]
    out = asyncio.run(target(body=body_cls(query="BWXT cash flow", max_rounds=3),
                             request=_token_request(["research:read", "research:launch"])))
    assert out["status"] == "running"
    kwargs = handler.start_research.call_args.kwargs
    assert kwargs["owner"] == "alice"
    assert kwargs["max_rounds"] == 3


def test_start_refuses_chat_attachment_from_token():
    router = setup_research_routes(_handler())
    target = _route(router, "/api/research/start", "POST")
    body_cls = target.__annotations__["body"]
    with pytest.raises(HTTPException) as exc:
        asyncio.run(target(body=body_cls(query="q", origin_chat_id="c1"),
                           request=_token_request(["research:read", "research:launch"])))
    assert exc.value.status_code == 400
