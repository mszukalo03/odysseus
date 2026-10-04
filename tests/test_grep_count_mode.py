"""grep's count mode: exact per-file tallies of matching lines.

Vault personas need to answer "how many entries are in X" without counting a
long markdown table by eye; count mode returns the numbers directly and scans
past the listing cap.
"""
import asyncio
import json

import pytest

from src.agent_tools.filesystem_tools import GrepTool, _CODENAV_MAX_HITS


def _run(content):
    return asyncio.run(GrepTool().execute(json.dumps(content), {}))


@pytest.fixture
def ws(tmp_path):
    table = ["| Added | Pri | Title |", "|---|---|---|"]
    table += [f"| 2026-09-0{i % 9 + 1} | P1 | Paper {i} |" for i in range(27)]
    (tmp_path / "queue.md").write_text("\n".join(table) + "\n")
    (tmp_path / "other.md").write_text("| P1 | x |\n| P1 | y |\n")
    big = "\n".join(f"| P1 | row {i} |" for i in range(_CODENAV_MAX_HITS + 50))
    (tmp_path / "big.md").write_text(big + "\n")
    # Look the module up at fixture time: other tests reload src.tool_execution,
    # and the grep tool resolves paths through whichever module is current.
    import importlib
    te = importlib.import_module("src.tool_execution")
    token = te._active_workspace.set(str(tmp_path))
    yield tmp_path
    te._active_workspace.reset(token)


def test_count_mode_tallies_one_file(ws):
    out = _run({"pattern": r"^\| 2026-", "path": "queue.md", "count": True})
    assert out["exit_code"] == 0, out
    assert out["output"].startswith("27 matching lines")


def test_count_mode_lists_per_file_and_total(ws):
    out = _run({"pattern": r"\| P1 \|", "count": True})["output"]
    first = out.splitlines()[0]
    assert first.startswith(f"{27 + 2 + _CODENAV_MAX_HITS + 50} matching lines")
    assert "in 3 file(s)" in first


def test_count_mode_zero_matches(ws):
    out = _run({"pattern": "nothing-here", "count": True})
    assert out["output"].startswith("0 matches")


def test_listing_mode_unchanged(ws):
    out = _run({"pattern": r"Paper 1\b", "path": "queue.md"})["output"]
    assert "queue.md" in out and "Paper 1" in out and "matching lines" not in out
