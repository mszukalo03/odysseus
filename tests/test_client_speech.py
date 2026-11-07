"""Tests for client-side device speech (static/js/deviceSpeech.js,
voiceRecorder.js, voiceMode.js, tts-ai.js, settings/speech.js) and CSP."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HELPER = _REPO / "tests" / "helpers" / "test_device_speech.mjs"
_HAS_NODE = shutil.which("node") is not None


def _run_node(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", *args],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )


def test_index_html_has_device_speech_settings_card():
    html = (_REPO / "static" / "index.html").read_text(encoding="utf-8")
    assert 'id="set-deviceSpeechCard"' in html
    assert 'id="set-deviceSpeechEnabled"' in html
    assert 'id="set-deviceSpeechUrl"' in html
    assert 'id="set-deviceSpeechSttModel"' in html
    assert 'id="set-deviceSpeechTtsModel"' in html
    assert 'id="set-deviceSpeechTtsVoice"' in html
    assert 'id="set-deviceSpeechTestBtn"' in html


def test_voice_mode_has_device_speech_gear_controls():
    vm = (_REPO / "static" / "js" / "voiceMode.js").read_text(encoding="utf-8")
    assert 'data-dev-k="enabled"' in vm
    assert 'data-dev-k="url"' in vm
    assert 'data-dev-k="sttModel"' in vm
    assert 'data-dev-k="ttsModel"' in vm
    assert 'data-dev-k="ttsVoice"' in vm
    assert "voice-mode-test-btn" in vm


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_device_speech_js_behavior():
    proc = _run_node("--experimental-vm-modules", str(_HELPER))
    assert proc.returncode == 0, (
        f"Node execution failed:\nSTDERR:\n{proc.stderr}\nSTDOUT:\n{proc.stdout}"
    )
    results = json.loads(proc.stdout.strip())
    assert results, "No test assertions returned"
    for item in results:
        assert item["pass"] is True, f"Failed: {item['name']} ({item.get('detail', '')})"
