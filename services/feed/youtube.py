import json
import logging
import re
import httpx

logger = logging.getLogger(__name__)

CHANNEL_RE = re.compile(
    r'(?:https?://)?(?:www\.|m\.)?youtube\.com/channel/(UC[\w-]{22})'
)
HANDLE_RE = re.compile(
    r'(?:https?://)?(?:www\.|m\.)?youtube\.com/@([\w.-]+)'
)
PLAYLIST_RE = re.compile(
    r'(?:https?://)?(?:www\.|m\.)?youtube\.com/playlist\?(?:.*?[&?])?list=(PL[\w-]+)'
)

_USER_AGENT = "Odysseus/1.0 RSS Reader"


def resolve_youtube_feed(url: str) -> str | None:
    """Convert a YouTube channel/playlist URL to its RSS feed URL."""
    url = url.strip()

    m = CHANNEL_RE.match(url)
    if m:
        return f"https://www.youtube.com/feeds/videos.xml?channel_id={m.group(1)}"

    m = PLAYLIST_RE.match(url)
    if m:
        return f"https://www.youtube.com/feeds/videos.xml?playlist_id={m.group(1)}"

    m = HANDLE_RE.match(url)
    if m:
        return _resolve_handle(m.group(1), url)

    return None


def _resolve_handle(handle: str, url: str) -> str | None:
    """Fetch a /@handle page and extract the channelId from ytInitialData."""
    try:
        resp = httpx.get(
            url,
            follow_redirects=True,
            timeout=15,
            headers={"User-Agent": _USER_AGENT},
        )
        resp.raise_for_status()
        m = re.search(r'ytInitialData\s*=\s*({.*?});', resp.text, re.DOTALL)
        if not m:
            logger.warning("No ytInitialData found on YouTube page %s", url)
            return None
        data = json.loads(m.group(1))
        cid = (
            data.get("metadata", {})
            .get("channelMetadataRenderer", {})
            .get("externalId")
        )
        if not cid and "error" in data.get("alerts", [{}])[0] if isinstance(data.get("alerts"), list) else False:
            logger.warning("YouTube page %s returned error", url)
            return None
        if not cid:
            logger.warning("Could not extract channelId from %s", url)
            return None
        return f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
    except Exception as e:
        logger.warning("Failed to resolve YouTube handle %s: %s", url, e)
        return None
