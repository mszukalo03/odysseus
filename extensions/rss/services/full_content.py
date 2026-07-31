import logging
import trafilatura
from typing import Optional

logger = logging.getLogger(__name__)


def extract_content(url: str, timeout: int = 30) -> Optional[dict]:
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return None
        text = trafilatura.extract(downloaded, output_format="markdown", with_metadata=True)
        if not text:
            return None
        return {"content": text}
    except Exception as e:
        logger.warning("Full-content extraction failed for %s: %s", url, e)
        return None
