"""Locate document.js and its extracted sibling modules for static-source
regression tests.

static/js/document.js is browser-coupled and not importable in pytest, so a
handful of tests grep/brace-match its raw source text instead. That made
those tests brittle to *which physical file* a function lives in — moving a
function out of document.js into a documentXxx.js sibling (the same kind of
extraction documentLibrary.js already did) silently broke assertions written
against document.js alone. See tests/test_email_open_dedup_js.py's fix in
commit ee2d77c5 for the same brittleness in emailLibrary.js.

These helpers search document.js AND every documentXxx.js sibling, so an
extraction that moves a function between those files needs no test changes.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC_JS = ROOT / "static" / "js"


def _document_module_paths():
    """document.js, plus every sibling module extracted from it — discovered
    by glob rather than a hardcoded list, so a new extraction doesn't require
    touching this file too."""
    yield STATIC_JS / "document.js"
    for path in sorted(STATIC_JS.glob("document*.js")):
        if path.name != "document.js":
            yield path


def document_module_sources() -> dict:
    """{Path: source text} for document.js and every documentXxx.js sibling
    that currently exists."""
    return {p: p.read_text(encoding="utf-8") for p in _document_module_paths() if p.exists()}


def combined_document_module_source() -> str:
    """Every document-module source concatenated, for simple `"x" in text`
    checks that don't care which specific file a string currently lives in."""
    return "\n".join(document_module_sources().values())


_FUNCTION_RE_TEMPLATE = r"(?:export\s+)?(?:async\s+)?function\s+{name}\([^)]*\)\s*\{{"


def function_body(name: str, sources: dict | None = None) -> str:
    """Brace-matched body of a top-level function `name` (without the
    surrounding `function name(...) { ... }`), searched across every document
    module. document.js's body is indented 2 spaces (a vestigial former IIFE)
    — the pattern doesn't anchor to line start, so that doesn't matter.

    Raises AssertionError naming every file searched if `name` isn't found in
    any of them — a clearer failure than a bare regex `None` when a function
    gets renamed or genuinely removed.
    """
    sources = sources if sources is not None else document_module_sources()
    pattern = re.compile(_FUNCTION_RE_TEMPLATE.format(name=re.escape(name)))
    for path, text in sources.items():
        match = pattern.search(text)
        if not match:
            continue
        start = match.end()
        depth = 1
        i = start
        while i < len(text) and depth:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        assert depth == 0, f"{name}() body did not close in {path}"
        return text[start : i - 1]
    raise AssertionError(
        f"{name}() not found in any of: {', '.join(str(p) for p in sources)}"
    )
