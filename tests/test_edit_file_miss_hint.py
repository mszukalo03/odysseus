"""edit_file names where a missed old_string stops matching (2026-11-22, W3).

The model anchored an inventory append on the last row plus an assumed
"---" and heading, which the file did not have. The bare "not found" error
sent it back to rereading a 70-line YAML list in slices.
"""
from src.agent_tools.filesystem_tools import _old_string_miss_hint

FILE = "---\nItems:\n  - a | one\n  - b | two\n---\n# Inventory\n"


def test_partial_match_names_the_first_differing_line():
    hint = _old_string_miss_hint(FILE, "  - b | two\n---\n### Inventory Hub")
    assert "first 2 line(s) of old_string match the file from line 4" in hint
    assert "'### Inventory Hub' where the file has '# Inventory'" in hint
    assert "use only that line as old_string" in hint


def test_no_match_at_all_says_so():
    assert "first line does not appear" in _old_string_miss_hint(FILE, "  - z | nope")


def test_mismatch_inside_the_first_line_is_a_first_line_miss():
    assert "first line does not appear" in _old_string_miss_hint(FILE, "  - b | two ")


def test_crlf_files_are_compared_by_line():
    crlf = FILE.replace("\n", "\r\n")
    hint = _old_string_miss_hint(crlf, "  - b | two\n---\n### Inventory Hub")
    assert "from line 4" in hint


def test_long_lines_are_clipped():
    hint = _old_string_miss_hint("x\n" + "y" * 300 + "\n", "x\n" + "z" * 300)
    assert "..." in hint and len(hint) < 450
