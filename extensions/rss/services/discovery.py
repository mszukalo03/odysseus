import logging
import re
import httpx
from typing import Optional
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def discover_feeds(page_url: str, timeout: int = 15) -> list[dict]:
    from services.feed.youtube import resolve_youtube_feed
    yt = resolve_youtube_feed(page_url)
    if yt:
        return [{"url": yt, "title": "YouTube Channel", "type": "rss"}]

    feeds = []
    try:
        resp = httpx.get(page_url, follow_redirects=True, timeout=timeout,
                         headers={"User-Agent": "Odysseus/1.0 RSS Reader"})
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        is_html = "html" in content_type

        if not is_html:
            if "xml" in content_type or page_url.endswith((".xml", ".rss", ".atom")):
                feeds.append({"url": str(resp.url), "title": str(resp.url), "type": "feed"})
            return feeds

        soup = BeautifulSoup(resp.text, "html.parser")
        for link in soup.find_all("link", type=re.compile(r"application/(rss|atom)\+xml", re.I)):
            href = link.get("href")
            if href:
                feed_url = str(httpx.URL(href) if href.startswith("http") else httpx.URL(str(resp.url)).join(href))
                feeds.append({
                    "url": feed_url,
                    "title": link.get("title", feed_url),
                    "type": "rss" if "rss" in (link.get("type") or "") else "atom",
                })
    except Exception as e:
        logger.warning("Feed discovery failed for %s: %s", page_url, e)
    return feeds
