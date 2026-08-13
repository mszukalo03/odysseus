"""Regression guards for AI document updates while Markdown Preview is visible (#2182).

Uses tests/helpers/document_js_sources.py's function_body(), which searches
document.js AND its extracted siblings, so this test survives a future
extraction moving either function out of document.js.
"""

from tests.helpers.document_js_sources import function_body


def test_markdown_preview_refresh_rerenders_visible_preview():
    body = function_body("_refreshMarkdownPreviewIfVisible")

    assert "_isMarkdownPreviewVisible()" in body
    assert "lang !== 'markdown'" in body
    assert "textarea.value = content;" in body
    assert "syncHighlighting();" in body
    assert "_setMarkdownPreviewActive(true, { remember: false });" in body


def test_doc_update_refreshes_preview_instead_of_hidden_editor_animation():
    body = function_body("handleDocUpdate")

    visible = "const markdownPreviewWasVisible = _isMarkdownPreviewVisible();"
    exit_preview = "if (markdownPreviewWasVisible) _setMarkdownPreviewActive(false, { remember: false });"
    diff = "enterDiffMode(oldContent, newContent);"
    refresh = "markdownPreviewWasVisible && _refreshMarkdownPreviewIfVisible(docId, newContent)"
    animate = "_animateDocEdit(textarea, newContent);"

    assert visible in body
    assert exit_preview in body
    assert diff in body
    assert body.index(exit_preview) < body.index(diff)
    assert refresh in body
    assert body.index(refresh) < body.index(animate)
    assert "_refreshMarkdownPreviewIfVisible(docId, newContent);" in body
