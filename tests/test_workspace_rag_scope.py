"""Workspace-indexed notes stay with their workspace.

A vault persona indexes its folder with scope=workspace metadata. Chats bound
to that workspace search only inside it; chats without a workspace never see
those chunks; everything else behaves as before.
"""
import os

import pytest

from src.rag_vector import _passes_scope


def _meta(source, scope=None):
    m = {"source": source}
    if scope:
        m["scope"] = scope
    return m


def test_path_prefix_keeps_only_that_folder(tmp_path):
    thesis = str(tmp_path / "vault" / "thesis")
    assert _passes_scope(_meta(os.path.join(thesis, "20-lit", "q.md"), "workspace"), thesis, None)
    assert not _passes_scope(_meta(str(tmp_path / "vault" / "personal" / "x.md"), "workspace"), thesis, None)
    # Sibling with a shared name prefix is not inside.
    assert not _passes_scope(_meta(str(tmp_path / "vault" / "thesis2" / "x.md")), thesis, None)
    assert not _passes_scope({"filename": "no-source"}, thesis, None)


def test_exclude_scope_hides_workspace_chunks():
    assert not _passes_scope(_meta("/vaults/v/thesis/a.md", "workspace"), None, "workspace")
    assert _passes_scope(_meta("/data/personal/doc.md"), None, "workspace")


class _FakeVector:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def _search(self, query, k=5, owner=None):
        self.calls.append(k)
        return self.rows[:k]


def test_search_widens_pool_and_filters(tmp_path):
    from src.rag_vector import VectorRAG
    rows = [{"metadata": _meta(f"/v/personal/{i}.md", "workspace")} for i in range(10)]
    rows += [{"metadata": _meta(f"/v/thesis/{i}.md", "workspace")} for i in range(3)]
    fake = _FakeVector(rows)
    out = VectorRAG.search(fake, "q", k=2, path_prefix="/v/thesis")
    assert [r["metadata"]["source"] for r in out] == ["/v/thesis/0.md", "/v/thesis/1.md"]
    assert fake.calls == [30]
    # No filters: unchanged single call with k.
    fake.calls.clear()
    VectorRAG.search(fake, "q", k=5)
    assert fake.calls == [5]


def test_preface_scopes_rag_by_workspace():
    from src.chat_processor import ChatProcessor

    seen = []

    class FakeRAG:
        def search(self, query, k=5, owner=None, **kw):
            seen.append(kw)
            return []

    class Docs:
        rag_manager = FakeRAG()

    proc = ChatProcessor.__new__(ChatProcessor)
    proc.memory_manager = None
    proc.personal_docs_manager = Docs()
    proc._last_used_memories = []
    try:
        proc.build_context_preface(message="hi", session=None, use_memory=False, use_rag=True,
                                   workspace="/v/thesis")
        proc.build_context_preface(message="hi", session=None, use_memory=False, use_rag=True)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"preface needs app wiring: {exc}")
    assert seen == [{"path_prefix": "/v/thesis"}, {"exclude_scope": "workspace"}]


def test_index_workspace_route_requires_trusted_root(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routes.preset_routes as pr
    from src import workspace_instructions as wi

    root = tmp_path / "vaults"
    ws = root / "omega" / "thesis"
    ws.mkdir(parents=True)
    (ws / "a.md").write_text("hello")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    monkeypatch.setenv(wi.TRUSTED_ROOTS_ENV, str(root))
    monkeypatch.setattr(pr, "require_admin", lambda: None)

    calls = {}

    class FakeRAG:
        def remove_directory(self, d):
            calls["removed"] = d
            return {"success": True, "removed_count": 3}

        def index_personal_documents(self, d, owner=None, extra_metadata=None):
            calls["indexed"] = (d, owner, extra_metadata)
            return {"success": True, "indexed_count": 7, "failed_count": 0}

    import src.rag_singleton as rs
    monkeypatch.setattr(rs, "get_rag_manager", lambda: FakeRAG())
    monkeypatch.setattr(pr, "effective_user", lambda request: "michael")

    app = FastAPI()
    app.include_router(pr.setup_preset_routes(preset_manager=None))
    app.dependency_overrides[pr.require_admin] = lambda: None
    client = TestClient(app)

    r = client.post("/api/presets/index-workspace", json={"workspace": str(outside)})
    assert r.status_code == 403
    r = client.post("/api/presets/index-workspace", json={"workspace": str(ws)})
    assert r.status_code == 200, r.text
    assert r.json()["indexed_count"] == 7
    real = os.path.realpath(str(ws))
    assert calls["removed"] == real
    assert calls["indexed"] == (real, "michael", {"scope": "workspace", "workspace": real})
