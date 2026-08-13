"""Per-feature display defaults (page vs popup) — src/settings.py accessors.

The nav shell (static/js/workspaceManager.js) trusts whatever this map returns
without a validation branch of its own, so the guarantee under test is that a
value coming out of here is always a real mode.
"""
import json

import pytest

from src import settings


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    """Point the settings store at a throwaway file for each test."""
    path = tmp_path / "settings.json"
    monkeypatch.setattr(settings, "SETTINGS_FILE", str(path))
    settings._invalidate_caches()
    yield path
    settings._invalidate_caches()


def _write(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")
    settings._invalidate_caches()


def test_defaults_are_returned_when_nothing_is_stored(settings_file):
    modes = settings.get_feature_display_modes()
    assert modes == settings.DEFAULT_SETTINGS["feature_display_modes"]
    # The editor keeps its historical chat-adjacent split pane by default —
    # migrating it to the nav shell must not silently move existing users to a
    # full-canvas page.
    assert modes["doc-editor"] == "popup"


def test_stored_choice_overrides_the_default(settings_file):
    _write(settings_file, {"feature_display_modes": {"doc-editor": "page"}})
    assert settings.get_feature_display_mode("doc-editor") == "page"
    # Features absent from the stored map keep their defaults rather than
    # disappearing from the merged view.
    assert settings.get_feature_display_mode("ithaca") == "page"


def test_unknown_feature_falls_back_to_the_supplied_default(settings_file):
    assert settings.get_feature_display_mode("not-a-feature") == "page"
    assert settings.get_feature_display_mode("not-a-feature", default="popup") == "popup"


@pytest.mark.parametrize("bad", ["", "PAGE", "window", None, 0, [], {}])
def test_corrupt_stored_modes_are_dropped_not_raised(settings_file, bad):
    """A hand-edited settings.json with a bad mode degrades to "no stored
    preference" — it must never surface a junk value the frontend would then
    have to defend against, and must never take the settings API down."""
    _write(settings_file, {"feature_display_modes": {"doc-editor": bad}})
    assert settings.get_feature_display_mode("doc-editor") == "popup"  # the default
    assert all(m in settings.FEATURE_DISPLAY_MODES
               for m in settings.get_feature_display_modes().values())


def test_non_dict_stored_value_is_ignored(settings_file):
    _write(settings_file, {"feature_display_modes": ["page"]})
    assert settings.get_feature_display_modes() == settings.DEFAULT_SETTINGS["feature_display_modes"]


def test_set_persists_and_returns_the_full_map(settings_file):
    modes = settings.set_feature_display_mode("doc-editor", "page")
    assert modes["doc-editor"] == "page"

    settings._invalidate_caches()
    assert settings.get_feature_display_mode("doc-editor") == "page"
    saved = json.loads(settings_file.read_text(encoding="utf-8"))
    assert saved["feature_display_modes"]["doc-editor"] == "page"


def test_set_preserves_other_features(settings_file):
    settings.set_feature_display_mode("doc-editor", "page")
    settings.set_feature_display_mode("rss", "popup")

    settings._invalidate_caches()
    modes = settings.get_feature_display_modes()
    assert modes["doc-editor"] == "page"
    assert modes["rss"] == "popup"
    assert modes["ithaca"] == "page"


def test_set_preserves_unrelated_settings(settings_file):
    _write(settings_file, {"default_model": "some-model"})
    settings.set_feature_display_mode("doc-editor", "page")

    settings._invalidate_caches()
    saved = json.loads(settings_file.read_text(encoding="utf-8"))
    assert saved["default_model"] == "some-model"


@pytest.mark.parametrize("bad", ["fullscreen", "", "Page", None, 1])
def test_set_rejects_an_unknown_mode(settings_file, bad):
    with pytest.raises(ValueError):
        settings.set_feature_display_mode("doc-editor", bad)
    assert not settings_file.exists()  # rejected before any write


def test_set_rejects_an_empty_feature_id(settings_file):
    with pytest.raises(ValueError):
        settings.set_feature_display_mode("", "page")
