"""Tests for the Argos /pair route (extensions/argos/backend.py) and the
optional-scopes addition to companion/pairing.py:mint_token.

Models tests/test_companion_pairing.py's fake-request pattern and its
stubbed core.database.
"""

import asyncio
import contextlib
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_CAPTURED = {}


class _ApiToken:
    def __init__(self, **kw):
        _CAPTURED.clear()
        _CAPTURED.update(kw)
        self.__dict__.update(kw)


@contextlib.contextmanager
def _get_db_session():
    yield MagicMock()


class _DBStub(types.ModuleType):
    def __getattr__(self, name):  # noqa: D401
        if name.startswith("__"):
            raise AttributeError(name)
        return MagicMock()


_db = _DBStub("core.database")
_db.get_db_session = _get_db_session
_db.ApiToken = _ApiToken


@pytest.fixture(autouse=True)
def _argos_pairing_stubs(monkeypatch):
    monkeypatch.setitem(sys.modules, "core.database", _db)


from fastapi import HTTPException  # noqa: E402

import companion.pairing as P  # noqa: E402
import extensions.argos.backend as argos  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


# --- mint_token: explicit scopes ---------------------------------------

def test_mint_token_accepts_explicit_scopes(monkeypatch):
    monkeypatch.setitem(sys.modules, "core.database", _db)
    token_id, raw = P.mint_token("alice", name="browser extension", scopes=["chat", "argos:ask"])
    assert raw.startswith("ody_")
    assert _CAPTURED["scopes"] == "chat,argos:ask"
    assert _CAPTURED["owner"] == "alice"
    assert _CAPTURED["name"] == "browser extension"


def test_mint_token_default_scopes_unchanged(monkeypatch):
    # Regression guard: existing companion callers (mint_token("alice")) must
    # still get plain "chat" — this is asserted by test_companion_pairing.py
    # too, but re-pinned here since it's the exact contract Argos depends on.
    monkeypatch.setitem(sys.modules, "core.database", _db)
    P.mint_token("alice")
    assert _CAPTURED["scopes"] == "chat"


def test_mint_token_deduplicates_and_strips_scope_list(monkeypatch):
    monkeypatch.setitem(sys.modules, "core.database", _db)
    P.mint_token("alice", scopes=[" chat ", "", "argos:ask"])
    assert _CAPTURED["scopes"] == "chat,argos:ask"


# --- /api/argos/pair: admin-only, POST-only, invalidates the cache -----

def _admin_mgr(is_admin):
    return SimpleNamespace(is_admin=lambda u: is_admin, is_configured=True)


def _pair_request(current_user="alice", *, api_token=False, is_admin=True,
                   sec_fetch_site=None, invalidate=None):
    return SimpleNamespace(
        state=SimpleNamespace(current_user=current_user, api_token=api_token),
        headers={"sec-fetch-site": sec_fetch_site} if sec_fetch_site else {},
        app=SimpleNamespace(
            state=SimpleNamespace(
                auth_manager=_admin_mgr(is_admin),
                invalidate_token_cache=invalidate or MagicMock(),
            )
        ),
    )


def _pair_endpoint():
    router = argos.setup()
    return next(r.endpoint for r in router.routes if r.path == "/api/argos/pair")


def test_pair_is_post_only():
    router = argos.setup()
    route = next(r for r in router.routes if r.path == "/api/argos/pair")
    assert route.methods == {"POST"}


def test_pair_requires_admin(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    endpoint = _pair_endpoint()
    req = _pair_request(current_user="bob", is_admin=False)
    with pytest.raises(HTTPException) as exc:
        _run(endpoint(req))
    assert exc.value.status_code == 403


def test_pair_rejects_cross_site():
    endpoint = _pair_endpoint()
    req = _pair_request(sec_fetch_site="cross-site")
    with pytest.raises(HTTPException) as exc:
        _run(endpoint(req))
    assert exc.value.status_code == 403


def test_pair_mints_scoped_token_and_invalidates_cache(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    mint = MagicMock(return_value=("id1", "ody_demo"))
    # backend.py does `from companion import pairing as _pairing` locally
    # inside the handler on every call, so patching the source module is
    # enough -- the fresh import resolves to this same patched object.
    import companion.pairing as pairing_mod
    monkeypatch.setattr(pairing_mod, "mint_token", mint)

    invalidate = MagicMock()
    endpoint = _pair_endpoint()
    req = _pair_request(invalidate=invalidate)

    result = _run(endpoint(req))

    mint.assert_called_once_with("alice", name="browser extension", scopes=["chat", "argos:ask"])
    invalidate.assert_called_once()
    assert result["token"] == "ody_demo"
    assert result["token_id"] == "id1"
    assert result["scopes"] == ["chat", "argos:ask"]
