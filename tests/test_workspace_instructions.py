"""Workspace instruction files (CLAUDE.md / AGENTS.md) and persona binding.

Covers the loader's trust gate, the root-to-workspace chain, @-import
expansion and its confinement, caching, the agent prompt's project-mode
switch, the persona (preset) binding of workspace + MCP allowlist, and the
MCP manager's allowlist helper.
"""
import os

import pytest

from src import workspace_instructions as wi


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """A two-sided vault like omegaV2: shared root file + per-side files."""
    root = tmp_path / "vaults" / "omega"
    (root / "thesis" / "00-core").mkdir(parents=True)
    (root / "personal").mkdir(parents=True)
    (root / "CLAUDE.md").write_text("ROOT RULES\n")
    (root / "thesis" / "CLAUDE.md").write_text("THESIS TOP\n@00-core/identity.md\nTHESIS END\n")
    (root / "thesis" / "00-core" / "identity.md").write_text("IDENTITY BODY\n@../../personal/secret.md\n")
    (root / "personal" / "CLAUDE.md").write_text("PERSONAL\n")
    (root / "personal" / "secret.md").write_text("PERSONAL SECRET\n")
    monkeypatch.setenv(wi.TRUSTED_ROOTS_ENV, str(tmp_path / "vaults"))
    wi._cache.clear()
    return root


def test_untrusted_workspace_loads_nothing(vault, monkeypatch):
    monkeypatch.setenv(wi.TRUSTED_ROOTS_ENV, "")
    assert wi.load_workspace_instructions(str(vault / "thesis")) == ""
    assert not wi.has_instructions(str(vault / "thesis"))


def test_filesystem_root_is_never_trusted(vault, monkeypatch):
    monkeypatch.setenv(wi.TRUSTED_ROOTS_ENV, os.sep)
    assert wi.trusted_roots() == []


def test_chain_runs_from_root_to_workspace(vault):
    text = wi.load_workspace_instructions(str(vault / "thesis"))
    assert text.index("ROOT RULES") < text.index("THESIS TOP") < text.index("THESIS END")
    assert "PERSONAL\n" not in text  # sibling side is not part of the chain


def test_imports_expand_in_place(vault):
    text = wi.load_workspace_instructions(str(vault / "thesis"))
    assert text.index("THESIS TOP") < text.index("IDENTITY BODY") < text.index("THESIS END")
    assert "@00-core/identity.md" not in text


def test_imports_stay_inside_the_trusted_root(vault, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("OUTSIDE\n")
    (vault / "thesis" / "CLAUDE.md").write_text(f"TOP\n@{outside}\n@../../../outside.md\n")
    wi._cache.clear()
    text = wi.load_workspace_instructions(str(vault / "thesis"))
    assert "OUTSIDE" not in text


def test_import_inside_code_fence_is_left_alone(vault):
    (vault / "thesis" / "CLAUDE.md").write_text("```\n@00-core/identity.md\n```\n")
    wi._cache.clear()
    text = wi.load_workspace_instructions(str(vault / "thesis"))
    assert "IDENTITY BODY" not in text
    assert "@00-core/identity.md" in text


def test_import_cycle_terminates(vault):
    a = vault / "thesis" / "a.md"
    b = vault / "thesis" / "b.md"
    a.write_text("A\n@b.md\n")
    b.write_text("B\n@a.md\n")
    (vault / "thesis" / "CLAUDE.md").write_text("@a.md\n")
    wi._cache.clear()
    text = wi.load_workspace_instructions(str(vault / "thesis"))
    assert text.count("\nA\n") <= 1 and "B" in text


def test_agents_md_wins_over_claude_md(vault):
    (vault / "thesis" / "AGENTS.md").write_text("AGENTS FILE\n")
    wi._cache.clear()
    text = wi.load_workspace_instructions(str(vault / "thesis"))
    assert "AGENTS FILE" in text and "THESIS TOP" not in text


def test_size_cap(vault, monkeypatch):
    monkeypatch.setattr(wi, "MAX_TOTAL_CHARS", 200)
    (vault / "thesis" / "CLAUDE.md").write_text("x" * 5000)
    wi._cache.clear()
    text = wi.load_workspace_instructions(str(vault / "thesis"))
    assert len(text) < 400 and "truncated" in text


def test_cache_refreshes_when_top_file_changes(vault):
    ws = str(vault / "personal")
    assert "PERSONAL" in wi.load_workspace_instructions(ws)
    path = vault / "personal" / "CLAUDE.md"
    path.write_text("CHANGED\n")
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    assert "CHANGED" in wi.load_workspace_instructions(ws)


def test_agent_prompt_switches_to_project_rules(vault):
    from src.agent_loop import _workspace_coding_rules, _workspace_project_rules
    assert "Workspace project mode" in _workspace_project_rules(str(vault / "thesis"))
    assert "Workspace coding mode" in _workspace_coding_rules(str(vault / "thesis"))
    # the switch itself is keyed on has_instructions()
    assert wi.has_instructions(str(vault / "thesis"))
    assert not wi.has_instructions(str(vault.parent.parent))  # above the trusted root


def test_preset_binding(tmp_path):
    from src.chat_handler import ChatHandler
    from src.preset_manager import PresetManager

    pm = PresetManager(str(tmp_path))
    pm.update_custom(1.0, 0, "persona", name="Thesis", workspace=" /vaults/omega/thesis ",
                     mcp_servers=["zotero", " ", "postgres"])
    handler = ChatHandler.__new__(ChatHandler)
    handler.preset_manager = pm
    assert handler.preset_binding("custom") == ("/vaults/omega/thesis", ["zotero", "postgres"])
    assert handler.preset_binding(None) == ("", [])

    pm.update_custom(1.0, 0, "persona", name="Thesis", enabled=False, workspace="/x")
    assert handler.preset_binding("custom") == ("", [])


def test_mcp_allowlist_hides_other_external_servers():
    from src.mcp_manager import McpManager

    mgr = McpManager.__new__(McpManager)
    mgr._tools = {
        "srv1": [{"name": "search"}, {"name": "get_item"}],
        "srv2": [{"name": "query"}],
        "builtin_memory": [{"name": "remember"}],
    }
    mgr._connections = {"srv1": {"name": "Zotero"}, "srv2": {"name": "Postgres"}}
    mgr.is_builtin = lambda sid: sid.startswith("builtin_")
    assert mgr.tool_names_outside(["zotero"]) == {"mcp__srv2__query"}
    assert mgr.tool_names_outside(["srv2"]) == {"mcp__srv1__search", "mcp__srv1__get_item"}
