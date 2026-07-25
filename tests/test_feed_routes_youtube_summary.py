from routes.feed_routes import _youtube_transcript_text


def test_non_youtube_url_returns_none_without_transcript_call(monkeypatch):
    async def _boom(*a, **k):
        raise AssertionError("should not attempt a transcript fetch for a non-YouTube URL")

    monkeypatch.setattr("services.youtube.youtube_handler.extract_transcript_async", _boom)
    assert _youtube_transcript_text("https://example.com/some-article") is None


def test_missing_url_returns_none():
    assert _youtube_transcript_text(None) is None
    assert _youtube_transcript_text("") is None


def test_youtube_url_returns_transcript_text(monkeypatch):
    async def _fake_transcript(url, video_id, max_retries=3):
        assert video_id == "dQw4w9WgXcQ"
        return {"success": True, "transcript": "never gonna give you up"}

    monkeypatch.setattr("services.youtube.youtube_handler.extract_transcript_async", _fake_transcript)
    text = _youtube_transcript_text("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert text == "never gonna give you up"


def test_youtube_url_with_no_transcript_available_returns_none(monkeypatch):
    async def _fake_transcript(url, video_id, max_retries=3):
        return {"success": False, "error": "no captions"}

    monkeypatch.setattr("services.youtube.youtube_handler.extract_transcript_async", _fake_transcript)
    assert _youtube_transcript_text("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is None
