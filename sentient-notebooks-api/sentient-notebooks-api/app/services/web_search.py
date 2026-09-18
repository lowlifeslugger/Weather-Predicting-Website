from typing import Dict, List

from app.core.config import settings

try:
    import trafilatura
    from ddgs import DDGS

    WEB_OK = True
except ImportError:
    WEB_OK = False


def web_search_available() -> bool:
    return WEB_OK


def retrieve_web(query: str) -> List[Dict]:
    """Search DuckDuckGo, scrape top pages, return list of {text, url, title}.
    Same behavior as rag.py: falls back to the search snippet if scraping a
    page fails, and silently returns [] on any hard failure rather than
    blowing up the whole chat request.
    """
    if not WEB_OK:
        return []

    results = []
    try:
        with DDGS() as ddgs:
            hits = list(ddgs.text(query, max_results=settings.web_results))

        for hit in hits:
            url = hit.get("href", "")
            title = hit.get("title", url)
            try:
                downloaded = trafilatura.fetch_url(url)
                text = trafilatura.extract(downloaded) or hit.get("body", "")
            except Exception:
                text = hit.get("body", "")

            if text and text.strip():
                # Trim to ~600 words so context window stays sane
                words = text.split()
                text = " ".join(words[:600])
                results.append({"text": text, "url": url, "title": title})
    except Exception:
        pass

    return results
