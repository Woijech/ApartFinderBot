"""Browser-backed HTML fetcher used as an optional source fallback.

CloakBrowser runs as a separate CDP service. This module keeps Playwright
imports lazy so normal HTTP-only deployments do not need a browser at runtime.
"""

from __future__ import annotations

from urllib.parse import urlencode, urlsplit, urlunsplit

from apartmentfinder.infrastructure.config import settings

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - exercised only without dependency.
    sync_playwright = None


class BrowserFetchError(RuntimeError):
    """Raised when the configured browser service cannot fetch a page."""


def fetch_html(url: str) -> str:
    """Render one URL through CloakBrowser CDP and return the page HTML."""
    if sync_playwright is None:
        raise BrowserFetchError("Playwright is not installed.")

    timeout_ms = int(settings.browser_fetch_timeout_seconds * 1000)
    cdp_url = browser_cdp_url()
    browser = None
    page = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(cdp_url)
            page = browser.new_page()
            page.goto(
                url,
                wait_until=settings.browser_fetch_wait_until,
                timeout=timeout_ms,
            )
            return page.content()
    except Exception as error:
        raise BrowserFetchError(f"Browser fetch failed: {url}") from error
    finally:
        if page is not None:
            try:
                page.close()
            except Exception:
                pass
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass


def browser_cdp_url() -> str:
    """Return the CDP URL, optionally scoped to one fingerprint seed."""
    seed = settings.browser_fetch_fingerprint_seed
    if not seed:
        return settings.browser_fetch_cdp_url

    parts = urlsplit(settings.browser_fetch_cdp_url)
    query = parts.query
    fingerprint_query = urlencode({"fingerprint": seed})
    if query:
        query = f"{query}&{fingerprint_query}"
    else:
        query = fingerprint_query
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))
