"""Regression guard: document.js must be imported under one URL everywhere.

ES module identity includes the query string, so `'./document.js'` and
`'./document.js?v=X'` instantiate TWO independent copies of the module — two
copies of `docs`, `activeDocId`, `isOpen`, `_pageHost`, etc. This was a real bug:
static/js/chat.js imported the unbusted URL while every other importer used a
`?v=` token, so `window.documentModule` (which sessions.js/slashCommands.js read)
pointed at a different live instance than the one static/app.js and
documentWorkspace.js held — verified live as disagreeing `isPanelOpen()` state.

document.js is browser-coupled and not importable in pytest, so this is a
source-grep guard rather than a runtime check: every specifier that resolves to
document.js, across every JS file and index.html, must be byte-identical.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"

# Matches import specifiers / script src pointing at document.js, capturing any
# trailing query string so a future accidental reintroduction is caught.
_SPECIFIER_RE = re.compile(r"""['"]([^'"]*\bdocument\.js(?:\?[^'"]*)?)['"]""")


def _document_js_specifiers():
    """Yield (file, specifier) for every reference to document.js in the tree."""
    for path in STATIC.rglob("*"):
        if path.suffix not in (".js", ".html"):
            continue
        if path.name == "document.js":
            continue  # the module itself, not an importer
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in _SPECIFIER_RE.finditer(text):
            specifier = match.group(1)
            if specifier.endswith("/document.js") or specifier == "document.js" or "/document.js?" in specifier or specifier.startswith("document.js?"):
                yield path.relative_to(ROOT), specifier


def test_every_document_js_import_uses_the_same_specifier_suffix():
    """No importer may add a query string another importer omits."""
    refs = list(_document_js_specifiers())
    assert refs, "expected to find references to document.js under static/"

    suffixes = {specifier.split("document.js", 1)[1] for _, specifier in refs}
    assert suffixes == {""}, (
        "document.js is imported with inconsistent query strings, which "
        f"creates duplicate module instances: {sorted(suffixes)} — offenders: "
        + ", ".join(f"{f}:{s}" for f, s in refs if not s.endswith("document.js"))
    )


def test_service_worker_precaches_the_same_url_document_js_is_imported_with():
    sw = (STATIC / "sw.js").read_text(encoding="utf-8")
    assert "'/static/js/document.js'" in sw, (
        "sw.js must precache document.js under the exact URL every importer "
        "uses (no query string) — see the module-identity note above"
    )
    assert "document.js?v=" not in sw
