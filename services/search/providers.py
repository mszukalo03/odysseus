"""Search provider implementations with a pluggable registry pattern."""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Callable, List, Optional
from urllib.parse import urljoin, urlparse, parse_qs

import httpx
from bs4 import BeautifulSoup

from src.constants import SEARXNG_INSTANCE, REQUEST_TIMEOUT, WEB_FETCH_USER_AGENT
from .analytics import RateLimitError, error_logger
from .query import build_enhanced_query

logger = logging.getLogger(__name__)

# ── Provider metadata ──

@dataclass
class ProviderInfo:
    """Metadata for a search provider."""
    id: str
    label: str
    needs_key: bool = False
    needs_url: bool = False
    key_setting: str = ""          # settings.json key for the API key
    env_var: str = ""              # env var for the API key
    hint: str = ""                 # UI hint shown in settings
    has_additional: list = field(default_factory=list)  # extra config fields like [{"key":"cx", "label":"CX ID", "placeholder":"..."}]


PROVIDER_REGISTRY: dict[str, ProviderInfo] = {
    "searxng": ProviderInfo(
        id="searxng", label="SearXNG", needs_url=True,
        hint="Self-hosted SearXNG instance. Leave URL empty to use the SEARXNG_INSTANCE env var.",
    ),
    "duckduckgo": ProviderInfo(
        id="duckduckgo", label="DuckDuckGo",
        hint="Free search — no API key required. Works out of the box.",
    ),
    "brave": ProviderInfo(
        id="brave", label="Brave Search", needs_key=True,
        key_setting="brave_api_key", env_var="DATA_BRAVE_API_KEY",
        hint="Get your API key from brave.com/search/api",
    ),
    "google_pse": ProviderInfo(
        id="google_pse", label="Google PSE", needs_key=True,
        key_setting="google_pse_key", env_var="GOOGLE_API_KEY",
        hint="Requires a Google API key and a Programmable Search Engine ID (CX). Create one at programmablesearchengine.google.com",
        has_additional=[{"key": "google_pse_cx", "label": "CX ID", "placeholder": "Google PSE engine ID"}],
    ),
    "tavily": ProviderInfo(
        id="tavily", label="Tavily", needs_key=True,
        key_setting="tavily_api_key", env_var="TAVILY_API_KEY",
        hint="AI-optimized search. 1,000 free credits/month at tavily.com",
    ),
    "serper": ProviderInfo(
        id="serper", label="Serper", needs_key=True,
        key_setting="serper_api_key", env_var="SERPER_API_KEY",
        hint="Google results via API. 2,500 free queries at serper.dev",
    ),
    "bing": ProviderInfo(
        id="bing", label="Bing", needs_key=True,
        key_setting="bing_api_key", env_var="BING_API_KEY",
        hint="Microsoft Bing Web Search API. Sign up at azure.microsoft.com/en-us/services/cognitive-services/bing-web-search-api",
    ),
    "search1api": ProviderInfo(
        id="search1api", label="Search1API", needs_key=True,
        key_setting="search1api_api_key", env_var="SEARCH1API_API_KEY",
        hint="Unified search API. 100 free queries/day at search1api.com",
    ),
    "firecrawl": ProviderInfo(
        id="firecrawl", label="Firecrawl", needs_key=True,
        key_setting="firecrawl_api_key", env_var="FIRECRAWL_API_KEY",
        hint="Web scraping and search API. Sign up at firecrawl.dev",
    ),
    "exa": ProviderInfo(
        id="exa", label="Exa", needs_key=True,
        key_setting="exa_api_key", env_var="EXA_API_KEY",
        hint="AI-powered search engine. Sign up at exa.ai",
    ),
    "disabled": ProviderInfo(
        id="disabled", label="Disabled",
        hint="Web search and deep research tools will be unavailable.",
    ),
}

# Backward-compatible PROVIDER_INFO: maps id → (label, needs_key, needs_url)
PROVIDER_INFO: dict[str, tuple[str, bool, bool]] = {
    pid: (info.label, info.needs_key, info.needs_url)
    for pid, info in PROVIDER_REGISTRY.items()
}


# ── Settings helpers ──

def _get_search_settings() -> dict:
    """Return search settings from admin config, falling back to env defaults."""
    try:
        from src.settings import load_settings
        return load_settings()
    except Exception:
        return {}


def _get_search_instance() -> str:
    """Return the active search API URL from admin settings, falling back to env var."""
    settings = _get_search_settings()
    url = (settings.get("search_url") or "").strip()
    if url:
        return url.rstrip("/")
    return SEARXNG_INSTANCE


def _get_provider_key(provider: str) -> str:
    """Return the API key for a specific provider, with legacy fallback."""
    settings = _get_search_settings()
    info = PROVIDER_REGISTRY.get(provider)
    if info and info.key_setting:
        val = (settings.get(info.key_setting) or "").strip()
        if val:
            return val
    # Legacy fallback: old shared search_api_key field
    legacy = (settings.get("search_api_key") or "").strip()
    if legacy:
        return legacy
    if info and info.env_var:
        return (os.environ.get(info.env_var) or "").strip()
    return ""


def _get_result_count() -> int:
    """Return configured result count, default 5."""
    settings = _get_search_settings()
    try:
        return int(settings.get("search_result_count", 5))
    except (ValueError, TypeError):
        return 5


# Canonical SafeSearch levels: "strict" (default), "moderate", "off".
# Each provider has its own knob name and value space -- see _safesearch_for(...).
_SAFESEARCH_LEVELS = ("strict", "moderate", "off")


def _get_safesearch_level() -> str:
    """Return configured SafeSearch level normalized to a canonical value."""
    settings = _get_search_settings()
    raw = (settings.get("search_safesearch") or "strict").strip().lower()
    if raw in _SAFESEARCH_LEVELS:
        return raw
    aliases = {
        "on": "strict", "high": "strict", "2": "strict",
        "medium": "moderate", "1": "moderate", "default": "moderate",
        "none": "off", "disabled": "off", "0": "off",
    }
    return aliases.get(raw, "strict")


def _safesearch_for(provider: str) -> Optional[str]:
    """Translate the canonical SafeSearch level into provider-specific values."""
    level = _get_safesearch_level()
    if provider == "searxng":
        return {"strict": "2", "moderate": "1", "off": "0"}[level]
    if provider == "brave":
        return level
    if provider == "duckduckgo_lib":
        return {"strict": "on", "moderate": "moderate", "off": "off"}[level]
    if provider == "duckduckgo_html":
        return {"strict": "1", "moderate": "-1", "off": "-2"}[level]
    if provider == "google_pse":
        return None if level == "off" else "active"
    if provider == "serper":
        return None if level == "off" else "active"
    if provider == "bing":
        return {"strict": "Strict", "moderate": "Moderate", "off": "Off"}[level]
    if provider == "search1api":
        return level
    if provider == "firecrawl":
        return None  # Firecrawl doesn't support SafeSearch
    if provider == "exa":
        return None  # Exa doesn't support SafeSearch
    return None


# ── SearXNG ──

_NEWS_HINTS = ("news", "nyheter", "headlines", "breaking", "latest", "today", "idag")

# Default general engines (google/duckduckgo/brave/startpage/wikipedia) are
# routinely rate-limited / CAPTCHA-blocked on this instance and return nothing.
# Pin engines that actually respond so non-news queries get results without any
# third-party API fallback. Override via SEARXNG_GENERAL_ENGINES.
_GENERAL_ENGINES = os.environ.get("SEARXNG_GENERAL_ENGINES", "bing,mojeek,presearch")


def searxng_search_api(query: str, count: Optional[int] = None, categories: str = "general",
                       time_filter: Optional[str] = None) -> List[dict]:
    """Search using SearXNG JSON API. Returns list of {title, url, snippet}."""
    count = count if count is not None else _get_result_count()
    instance = _get_search_instance()
    api_key = ""
    headers = {"User-Agent": WEB_FETCH_USER_AGENT}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    # News/fresh queries do badly in the 'general' category — it favours
    # encyclopedic/tourism pages, ignores recency, and (with no language pin)
    # bleeds in foreign-language results. When the agent layer detected
    # freshness (time_filter) or the query reads like a news lookup, switch to
    # the 'news' category, constrain recency, and pin language to English so a
    # search like "Canada latest news" returns actual news instead of Wikipedia.
    # Pin English for ALL searches — without it, SearXNG geolocates / mixes
    # languages and brand-ambiguous terms bleed in foreign SEO pages (e.g.
    # "Odyssey" → Honda Japan, "Trojan" → Japanese malware blogs, "Polyphemus"
    # → Chinese math forums). The news path already did this; general didn't.
    params = {
        "q": query,
        "format": "json",
        "language": "en",
        "safesearch": _safesearch_for("searxng"),
    }
    q_lc = query.lower()
    is_news = time_filter is not None or any(h in q_lc for h in _NEWS_HINTS)
    if is_news and categories == "general":
        params["categories"] = "news"
        if time_filter in ("day", "week", "month", "year"):
            # 'day' is too sparse on most SearXNG news engines — widen to a week
            # so there's enough volume; the news category already biases recent.
            params["time_range"] = "week" if time_filter in ("day", "week") else time_filter
    else:
        params["categories"] = categories
        # Route general queries to engines that aren't blocked (default general
        # set returns 0 on this instance — see _GENERAL_ENGINES).
        if categories == "general" and _GENERAL_ENGINES:
            params["engines"] = _GENERAL_ENGINES
    try:
        def _parse_results(results):
            return [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("content", ""),
                }
                for r in results[:count]
                if r.get("url")
            ]

        def _run(search_params):
            response = httpx.get(
                f"{instance}/search",
                params=search_params,
                headers=headers or None,
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
            return _parse_results(data.get("results", [])), data

        active_params = params
        parsed, data = _run(active_params)
        if not parsed and is_news and categories == "general":
            # Some self-hosted SearXNG configs have no working news engines.
            # Fall back to the known-good general engines before reporting an
            # empty search, otherwise common queries like "Canada news" fail.
            fallback = {
                "q": query,
                "format": "json",
                "language": "en",
                "categories": "general",
                "safesearch": _safesearch_for("searxng"),
            }
            if _GENERAL_ENGINES:
                fallback["engines"] = _GENERAL_ENGINES
            logger.info(
                "SearXNG news search returned 0 results for %r; retrying general engines",
                query,
            )
            active_params = fallback
            parsed, data = _run(active_params)
        if not parsed and active_params.get("language"):
            fallback = dict(active_params)
            fallback.pop("language", None)
            logger.info(
                "SearXNG language-pinned search returned 0 results for %r; retrying without language",
                query,
            )
            active_params = fallback
            parsed, data = _run(active_params)
        if not parsed and active_params.get("engines"):
            fallback = dict(active_params)
            fallback.pop("engines", None)
            logger.info(
                "SearXNG pinned engines returned 0 results for %r; retrying default engines",
                query,
            )
            parsed, data = _run(fallback)
        logger.info(f"SearXNG JSON API returned {len(parsed)} results for: {query}")
        if not parsed:
            unresponsive = data.get("unresponsive_engines") if isinstance(data, dict) else None
            if unresponsive:
                logger.info(f"SearXNG unresponsive engines for {query!r}: {unresponsive}")
        return parsed
    except Exception as e:
        logger.warning(f"SearXNG JSON API search failed: {e}")
        html_results = searxng_search(query, max_results=count)
        if html_results:
            logger.info(f"SearXNG HTML fallback returned {len(html_results)} results for: {query}")
        return html_results


def searxng_search(query, max_results=10):
    """Search using SearXNG instance - parsing HTML."""
    instance = _get_search_instance()
    api_key = ""
    req_headers = {"User-Agent": WEB_FETCH_USER_AGENT}
    if api_key:
        req_headers["Authorization"] = f"Bearer {api_key}"
    try:
        response = httpx.get(
            f"{instance}/search",
            params={"q": query, "safesearch": _safesearch_for("searxng")},
            headers=req_headers,
            timeout=10,
        )
        if response.is_success:
            soup = BeautifulSoup(response.text, "html.parser")
            results = []
            for article in soup.select("article.result")[:max_results]:
                title_elem = article.select_one("h3 a")
                if not title_elem:
                    continue
                title = title_elem.get_text(strip=True)
                url = title_elem.get("href", "")
                snippet_elem = article.select_one("p.content")
                snippet = snippet_elem.get_text(strip=True) if snippet_elem else ""
                results.append({"title": title, "url": url, "snippet": snippet})
            logger.info(f"SearXNG search (HTML) returned {len(results)} results")
            return results
    except Exception as e:
        logger.error(f"SearXNG search failed: {e}")
    return []


# ── Brave ──

def brave_search(query: str, count: Optional[int] = None, time_filter: Optional[str] = None) -> List[dict]:
    """Search using Brave API with key from admin settings or env var."""
    count = count if count is not None else _get_result_count()
    api_key = _get_provider_key("brave") or os.environ.get("DATA_BRAVE_API_KEY") or ""
    return _brave_search_impl(query, count, time_filter, search_config={"brave_api_key": api_key})


def _brave_search_impl(query: str, count: int, time_filter: Optional[str] = None, search_config: dict = None) -> List[dict]:
    """Core Brave API call. Returns a list of result dicts or an empty list on failure."""
    enhanced_query = build_enhanced_query(query, time_filter)
    config = search_config or {}

    brave_api_key = config.get("brave_api_key")
    if not brave_api_key:
        brave_api_key = os.environ.get("DATA_BRAVE_API_KEY")

    if not brave_api_key:
        logger.warning("Brave API key not found, returning empty results for fallback")
        return []

    headers = {"X-Subscription-Token": brave_api_key, "Accept": "application/json"}
    params = {
        "q": enhanced_query,
        "count": count,
        "safesearch": _safesearch_for("brave"),
    }
    if time_filter:
        time_map = {"day": "day", "week": "week", "month": "month", "year": "year"}
        if time_filter in time_map:
            params["freshness"] = time_map[time_filter]

    logger.info(f"Executing Brave search with query: {enhanced_query}")
    try:
        response = httpx.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers=headers,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 429:
            raise RateLimitError("Brave rate limit hit")
        response.raise_for_status()
    except httpx.RequestError as e:
        error_logger.error(f"NetworkError during Brave search: {e}")
        return []
    except RateLimitError as e:
        error_logger.error(str(e))
        return []

    try:
        data = response.json()
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Brave API response: {e}")
        return []

    results = []
    if "web" in data and "results" in data["web"]:
        for item in data["web"]["results"][:count]:
            url = item.get("url", "")
            if not url:
                continue
            results.append({
                "title": item.get("title", ""),
                "url": url,
                "snippet": item.get("description", "") or item.get("content", ""),
                "age": item.get("date", "") if item.get("date") else "",
            })

    logger.info(f"Brave search returned {len(results)} results")
    return results


# ── DuckDuckGo (free, no key) ──

def _is_duckduckgo_host(host: str) -> bool:
    """True only for duckduckgo.com and its subdomains."""
    host = (host or "").lower()
    return host == "duckduckgo.com" or host.endswith(".duckduckgo.com")


def _resolve_ddg_redirect(raw: str) -> str:
    """Resolve a DuckDuckGo /l/?uddg= redirect URL to its destination."""
    if not raw:
        return raw
    resolved = raw
    if resolved.startswith("//"):
        resolved = "https:" + resolved
    elif resolved.startswith("/"):
        resolved = urljoin("https://html.duckduckgo.com", resolved)
    try:
        parsed = urlparse(resolved)
        if _is_duckduckgo_host(parsed.hostname) and parsed.path.rstrip("/") == "/l":
            qs = parse_qs(parsed.query)
            if "uddg" in qs:
                return qs["uddg"][0]
    except Exception:
        pass
    return resolved


def duckduckgo_search(query: str, count: Optional[int] = None, time_filter: Optional[str] = None) -> List[dict]:
    """Search using DuckDuckGo via the duckduckgo-search library. No API key needed."""
    count = count if count is not None else _get_result_count()
    def _html_fallback() -> List[dict]:
        try:
            response = httpx.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query, "kp": _safesearch_for("duckduckgo_html")},
                headers={"User-Agent": WEB_FETCH_USER_AGENT},
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            parsed = []
            for result in soup.select(".result")[:count]:
                link = result.select_one(".result__a")
                if not link:
                    continue
                url = _resolve_ddg_redirect(link.get("href", ""))
                if not url:
                    continue
                snippet_el = result.select_one(".result__snippet")
                parsed.append({
                    "title": link.get_text(" ", strip=True),
                    "url": url,
                    "snippet": snippet_el.get_text(" ", strip=True) if snippet_el else "",
                })
            logger.info(f"DuckDuckGo HTML search returned {len(parsed)} results")
            return parsed
        except Exception as e:
            logger.warning(f"DuckDuckGo HTML search failed: {e}")
            return []

    try:
        from ddgs import DDGS
    except ImportError:
        logger.warning("duckduckgo-search package not installed; using HTML fallback")
        return _html_fallback()

    timelimit = None
    if time_filter:
        time_map = {"day": "d", "week": "w", "month": "m", "year": "y"}
        timelimit = time_map.get(time_filter)

    try:
        ddgs = DDGS()
        raw = ddgs.text(
            query,
            max_results=count,
            timelimit=timelimit,
            safesearch=_safesearch_for("duckduckgo_lib"),
        )
        results = []
        for item in raw:
            url = item.get("href", "")
            if not url:
                continue
            results.append({
                "title": item.get("title", ""),
                "url": url,
                "snippet": item.get("body", ""),
            })
        logger.info(f"DuckDuckGo search returned {len(results)} results")
        return results or _html_fallback()
    except Exception as e:
        logger.warning(f"DuckDuckGo search failed: {e}")
        return _html_fallback()


# ── Google Programmable Search Engine ──

def google_pse_search(query: str, count: Optional[int] = None, time_filter: Optional[str] = None) -> List[dict]:
    """Search using Google PSE (Custom Search JSON API).

    Requires two keys in settings:
      - search_api_key: Google API key
      - google_pse_cx: Programmable Search Engine ID (cx)
    Or env vars GOOGLE_API_KEY and GOOGLE_PSE_CX.
    """
    count = count if count is not None else _get_result_count()
    settings = _get_search_settings()
    api_key = _get_provider_key("google_pse") or os.environ.get("GOOGLE_API_KEY", "")
    cx = (settings.get("google_pse_cx") or "").strip() or os.environ.get("GOOGLE_PSE_CX", "")

    if not api_key or not cx:
        logger.warning("Google PSE: missing API key or CX ID")
        return []

    params = {
        "key": api_key,
        "cx": cx,
        "q": query,
        "num": min(count, 10),  # Google PSE max is 10 per request
    }
    safe = _safesearch_for("google_pse")
    if safe:
        params["safe"] = safe
    if time_filter:
        # dateRestrict: d[number], w[number], m[number], y[number]
        time_map = {"day": "d1", "week": "w1", "month": "m1", "year": "y1"}
        if time_filter in time_map:
            params["dateRestrict"] = time_map[time_filter]

    try:
        response = httpx.get(
            "https://www.googleapis.com/customsearch/v1",
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 429:
            raise RateLimitError("Google PSE rate limit hit")
        response.raise_for_status()
    except httpx.RequestError as e:
        error_logger.error(f"Google PSE search failed: {e}")
        return []
    except RateLimitError as e:
        error_logger.error(str(e))
        return []

    try:
        data = response.json()
    except json.JSONDecodeError as e:
        error_logger.error(f"Google PSE returned invalid JSON: {e}")
        return []

    results = []
    for item in data.get("items", [])[:count]:
        url = item.get("link", "")
        if not url:
            continue
        results.append({
            "title": item.get("title", ""),
            "url": url,
            "snippet": item.get("snippet", ""),
        })

    logger.info(f"Google PSE returned {len(results)} results")
    return results


# ── Tavily ──

def tavily_search(query: str, count: Optional[int] = None, time_filter: Optional[str] = None) -> List[dict]:
    """Search using Tavily API. Requires search_api_key or TAVILY_API_KEY env var."""
    count = count if count is not None else _get_result_count()
    api_key = _get_provider_key("tavily") or os.environ.get("TAVILY_API_KEY", "")
    if not api_key:
        logger.warning("Tavily: no API key configured")
        return []

    payload = {
        "query": query,
        "max_results": count,
        "include_answer": False,
    }
    if time_filter:
        time_map = {"day": "day", "week": "week", "month": "month", "year": "year"}
        if time_filter in time_map:
            payload["days"] = {"day": 1, "week": 7, "month": 30, "year": 365}[time_filter]

    try:
        response = httpx.post(
            "https://api.tavily.com/search",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 429:
            raise RateLimitError("Tavily rate limit hit")
        response.raise_for_status()
    except httpx.RequestError as e:
        error_logger.error(f"Tavily search failed: {e}")
        return []
    except RateLimitError as e:
        error_logger.error(str(e))
        return []

    try:
        data = response.json()
    except json.JSONDecodeError as e:
        error_logger.error(f"Tavily returned invalid JSON: {e}")
        return []

    results = []
    for item in data.get("results", [])[:count]:
        url = item.get("url", "")
        if not url:
            continue
        results.append({
            "title": item.get("title", ""),
            "url": url,
            "snippet": item.get("content", ""),
            "age": item.get("published_date", ""),
        })

    logger.info(f"Tavily returned {len(results)} results")
    return results


# ── Serper.dev ──

def serper_search(query: str, count: Optional[int] = None, time_filter: Optional[str] = None) -> List[dict]:
    """Search using Serper.dev API. Requires search_api_key or SERPER_API_KEY env var."""
    count = count if count is not None else _get_result_count()
    api_key = _get_provider_key("serper") or os.environ.get("SERPER_API_KEY", "")
    if not api_key:
        logger.warning("Serper: no API key configured")
        return []

    payload = {
        "q": query,
        "num": count,
    }
    safe = _safesearch_for("serper")
    if safe:
        payload["safe"] = safe
    if time_filter:
        time_map = {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"}
        if time_filter in time_map:
            payload["tbs"] = time_map[time_filter]

    try:
        response = httpx.post(
            "https://google.serper.dev/search",
            json=payload,
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 429:
            raise RateLimitError("Serper rate limit hit")
        response.raise_for_status()
    except httpx.RequestError as e:
        error_logger.error(f"Serper search failed: {e}")
        return []
    except RateLimitError as e:
        error_logger.error(str(e))
        return []

    try:
        data = response.json()
    except json.JSONDecodeError as e:
        error_logger.error(f"Serper returned invalid JSON: {e}")
        return []

    results = []
    for item in data.get("organic", [])[:count]:
        url = item.get("link", "")
        if not url:
            continue
        results.append({
            "title": item.get("title", ""),
            "url": url,
            "snippet": item.get("snippet", ""),
            "age": item.get("date", ""),
        })

    logger.info(f"Serper returned {len(results)} results")
    return results


# ── Bing Web Search ──

def bing_search(query: str, count: int = 10, time_filter: Optional[str] = None) -> List[dict]:
    """Search using Microsoft Bing Web Search API. Requires BING_API_KEY env var or bing_api_key setting."""
    api_key = _get_provider_key("bing") or os.environ.get("BING_API_KEY", "")
    if not api_key:
        logger.warning("Bing: no API key configured")
        return []

    params = {
        "q": query,
        "count": count,
        "textFormat": "Raw",
        "safeSearch": _safesearch_for("bing"),
    }
    if time_filter:
        time_map = {"day": "Day", "week": "Week", "month": "Month", "year": "Year"}
        if time_filter in time_map:
            params["freshness"] = time_map[time_filter]

    try:
        response = httpx.get(
            "https://api.bing.microsoft.com/v7.0/search",
            params=params,
            headers={
                "Ocp-Apim-Subscription-Key": api_key,
                "User-Agent": WEB_FETCH_USER_AGENT,
            },
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 429:
            raise RateLimitError("Bing rate limit hit")
        response.raise_for_status()
    except httpx.RequestError as e:
        error_logger.error(f"NetworkError during Bing search: {e}")
        return []
    except RateLimitError as e:
        error_logger.error(str(e))
        return []

    try:
        data = response.json()
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Bing API response: {e}")
        return []

    results = []
    for item in data.get("webPages", {}).get("value", [])[:count]:
        url = item.get("url", "")
        if not url:
            continue
        results.append({
            "title": item.get("name", ""),
            "url": url,
            "snippet": item.get("snippet", ""),
            "age": item.get("datePublished", "") or "",
        })

    logger.info(f"Bing search returned {len(results)} results")
    return results


# ── Search1API ──

def search1api_search(query: str, count: int = 10, time_filter: Optional[str] = None) -> List[dict]:
    """Search using Search1API. Requires SEARCH1API_API_KEY env var or search1api_api_key setting."""
    api_key = _get_provider_key("search1api") or os.environ.get("SEARCH1API_API_KEY", "")
    if not api_key:
        logger.warning("Search1API: no API key configured")
        return []

    payload = {
        "query": query,
        "max_results": count,
    }
    if time_filter:
        time_map = {"day": "day", "week": "week", "month": "month", "year": "year"}
        if time_filter in time_map:
            payload["time_filter"] = time_map[time_filter]

    try:
        response = httpx.post(
            "https://www.search1api.com/search",
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 429:
            raise RateLimitError("Search1API rate limit hit")
        response.raise_for_status()
    except httpx.RequestError as e:
        error_logger.error(f"NetworkError during Search1API search: {e}")
        return []
    except RateLimitError as e:
        error_logger.error(str(e))
        return []

    try:
        data = response.json()
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Search1API response: {e}")
        return []

    results = []
    for item in data.get("results", [])[:count]:
        url = item.get("link") or item.get("url", "")
        if not url:
            continue
        results.append({
            "title": item.get("title", ""),
            "url": url,
            "snippet": item.get("snippet") or item.get("content", ""),
            "age": item.get("publishedDate") or item.get("date", "") or "",
        })

    logger.info(f"Search1API returned {len(results)} results")
    return results


# ── Firecrawl ──

def firecrawl_search(query: str, count: int = 10, time_filter: Optional[str] = None) -> List[dict]:
    """Search using Firecrawl API. Requires FIRECRAWL_API_KEY env var or firecrawl_api_key setting."""
    api_key = _get_provider_key("firecrawl") or os.environ.get("FIRECRAWL_API_KEY", "")
    if not api_key:
        logger.warning("Firecrawl: no API key configured")
        return []

    payload = {
        "query": query,
        "maxResults": count,
    }
    # Firecrawl search doesn't appear to support time_filter or SafeSearch

    try:
        response = httpx.post(
            "https://api.firecrawl.dev/v1/search",
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 429:
            raise RateLimitError("Firecrawl rate limit hit")
        response.raise_for_status()
    except httpx.RequestError as e:
        error_logger.error(f"NetworkError during Firecrawl search: {e}")
        return []
    except RateLimitError as e:
        error_logger.error(str(e))
        return []

    try:
        data = response.json()
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Firecrawl API response: {e}")
        return []

    results = []
    raw = data.get("data", data.get("results", []))
    for item in raw[:count]:
        url = item.get("url", "")
        if not url:
            continue
        results.append({
            "title": item.get("title", "") or item.get("name", ""),
            "url": url,
            "snippet": item.get("description", "") or item.get("text", "") or item.get("snippet", ""),
        })

    logger.info(f"Firecrawl returned {len(results)} results")
    return results


# ── Exa ──

def exa_search(query: str, count: int = 10, time_filter: Optional[str] = None) -> List[dict]:
    """Search using Exa AI search API. Requires EXA_API_KEY env var or exa_api_key setting."""
    api_key = _get_provider_key("exa") or os.environ.get("EXA_API_KEY", "")
    if not api_key:
        logger.warning("Exa: no API key configured")
        return []

    payload = {
        "query": query,
        "numResults": count,
    }
    if time_filter:
        time_map = {"day": "day", "week": "week", "month": "month", "year": "year"}
        if time_filter in time_map:
            payload["start_published_date"] = time_map[time_filter]

    try:
        response = httpx.post(
            "https://api.exa.ai/search",
            json=payload,
            headers={
                "x-api-key": api_key,
                "Content-Type": "application/json",
            },
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 429:
            raise RateLimitError("Exa rate limit hit")
        response.raise_for_status()
    except httpx.RequestError as e:
        error_logger.error(f"NetworkError during Exa search: {e}")
        return []
    except RateLimitError as e:
        error_logger.error(str(e))
        return []

    try:
        data = response.json()
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Exa API response: {e}")
        return []

    results = []
    for item in data.get("results", [])[:count]:
        url = item.get("url", "")
        if not url:
            continue
        results.append({
            "title": item.get("title", ""),
            "url": url,
            "snippet": item.get("text", "") or item.get("snippet", ""),
            "age": item.get("publishedDate", "") or "",
        })

    logger.info(f"Exa returned {len(results)} results")
    return results


# ── Provider function dispatch ──
# Registered here (after all function definitions) so refs are available.

PROVIDER_FUNCTIONS: dict[str, Callable] = {
    "searxng": searxng_search_api,
    "brave": brave_search,
    "duckduckgo": duckduckgo_search,
    "google_pse": google_pse_search,
    "tavily": tavily_search,
    "serper": serper_search,
    "bing": bing_search,
    "search1api": search1api_search,
    "firecrawl": firecrawl_search,
    "exa": exa_search,
}
