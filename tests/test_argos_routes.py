"""Tests for extensions/argos/backend.py: the bearer-token scope gate and
route registration. Models tests/test_ithaca_routes.py."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import extensions.argos.backend as argos


def _fake_request(api_token=False, scopes=None, owner=None, current_user=None):
    state = SimpleNamespace(
        api_token=api_token,
        api_token_scopes=scopes or [],
        api_token_owner=owner,
        current_user=current_user,
    )
    return SimpleNamespace(state=state)


def test_scope_gate_cookie_session_passes_through_to_current_user():
    req = _fake_request(api_token=False, current_user="alice")
    assert argos._scope_owner(req) == "alice"


def test_scope_gate_token_with_scope_passes():
    req = _fake_request(api_token=True, scopes=["argos:ask"], owner="alice")
    assert argos._scope_owner(req) == "alice"


def test_scope_gate_token_without_scope_rejected():
    req = _fake_request(api_token=True, scopes=["chat"], owner="alice")
    with pytest.raises(HTTPException) as exc:
        argos._scope_owner(req)
    assert exc.value.status_code == 403


def test_scope_gate_token_with_comma_string_scopes_passes():
    # The auth middleware always stamps a list (app.py:_refresh_token_cache),
    # but companion/routes.py defensively handles a comma-string too — match
    # that idiom here in case a future caller stamps request.state directly.
    req = _fake_request(api_token=True, scopes="chat,argos:ask", owner="alice")
    assert argos._scope_owner(req) == "alice"


def test_scope_gate_token_without_owner_rejected():
    req = _fake_request(api_token=True, scopes=["argos:ask"], owner=None)
    with pytest.raises(HTTPException) as exc:
        argos._scope_owner(req)
    assert exc.value.status_code == 403


def test_argos_scope_registered_for_tokens_when_enabled(monkeypatch):
    # argos ships enabled_by_default=false (it mints credentials, so an admin
    # must opt in) — mirror that opt-in the way ODYSSEUS_EXTENSIONS_ENABLED
    # or the Settings > Extensions toggle would.
    monkeypatch.setenv("ODYSSEUS_EXTENSIONS_ENABLED", "argos")
    from routes.api_token_routes import _allowed_scopes
    assert "argos:ask" in _allowed_scopes()


def test_argos_disabled_by_default():
    from src import extension_host
    ext = next(e for e in extension_host.discover() if e.id == "argos")
    assert ext.manifest.get("enabled_by_default") is False


def test_router_registers_expected_routes():
    router = argos.setup()
    paths = {r.path for r in router.routes}
    assert paths == {
        "/api/argos/hello",
        "/api/argos/context",
        "/api/argos/pair",
        "/api/argos/browser-extension.zip",
    }
