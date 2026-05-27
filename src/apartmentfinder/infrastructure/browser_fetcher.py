"""Browser-backed HTML fetcher used as an optional source fallback."""

from __future__ import annotations

from apartmentfinder.infrastructure.config import settings

try:
    from cloakbrowser import launch
except ImportError:  # pragma: no cover - exercised only without dependency.
    launch = None


class BrowserFetchError(RuntimeError):
    """Raised when the configured browser service cannot fetch a page."""


def fetch_html(url: str) -> str:
    """Render one URL through CloakBrowser and return the page HTML."""
    if launch is None:
        raise BrowserFetchError("CloakBrowser is not installed.")

    timeout_ms = int(settings.browser_fetch_timeout_seconds * 1000)
    browser = None
    page = None
    try:
        browser = launch(headless=True)
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
