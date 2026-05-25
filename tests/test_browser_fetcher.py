import pytest

from apartmentfinder.infrastructure import browser_fetcher
from apartmentfinder.infrastructure.browser_fetcher import BrowserFetchError


class FakePage:
    def __init__(self, html="<html>ok</html>", error=None):
        self.html = html
        self.error = error
        self.goto_args = None
        self.closed = False

    def goto(self, url, wait_until, timeout):
        self.goto_args = {
            "url": url,
            "wait_until": wait_until,
            "timeout": timeout,
        }
        if self.error is not None:
            raise self.error

    def content(self):
        return self.html

    def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self, page):
        self.page = page
        self.closed = False

    def new_page(self):
        return self.page

    def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self, browser):
        self.browser = browser
        self.cdp_url = None

    def connect_over_cdp(self, cdp_url):
        self.cdp_url = cdp_url
        return self.browser


class FakePlaywright:
    def __init__(self, chromium):
        self.chromium = chromium


class FakeSyncPlaywright:
    def __init__(self, playwright):
        self.playwright = playwright

    def __enter__(self):
        return self.playwright

    def __exit__(self, *_args):
        return None


def install_fake_playwright(monkeypatch, page):
    browser = FakeBrowser(page)
    chromium = FakeChromium(browser)
    playwright = FakePlaywright(chromium)
    monkeypatch.setattr(
        browser_fetcher,
        "sync_playwright",
        lambda: FakeSyncPlaywright(playwright),
    )
    return browser, chromium


def test_browser_fetcher_fetches_html_with_configured_goto_options(monkeypatch):
    page = FakePage("<html>rendered</html>")
    browser, chromium = install_fake_playwright(monkeypatch, page)
    monkeypatch.setattr(browser_fetcher.settings, "browser_fetch_cdp_url", "http://c:9222")
    monkeypatch.setattr(
        browser_fetcher.settings,
        "browser_fetch_fingerprint_seed",
        None,
    )
    monkeypatch.setattr(browser_fetcher.settings, "browser_fetch_timeout_seconds", 12)
    monkeypatch.setattr(browser_fetcher.settings, "browser_fetch_wait_until", "load")

    html = browser_fetcher.fetch_html("https://example.test/page")

    assert html == "<html>rendered</html>"
    assert chromium.cdp_url == "http://c:9222"
    assert page.goto_args == {
        "url": "https://example.test/page",
        "wait_until": "load",
        "timeout": 12000,
    }
    assert page.closed is True
    assert browser.closed is True


def test_browser_fetcher_adds_fingerprint_seed_to_cdp_url(monkeypatch):
    page = FakePage()
    _browser, chromium = install_fake_playwright(monkeypatch, page)
    monkeypatch.setattr(
        browser_fetcher.settings,
        "browser_fetch_cdp_url",
        "http://c:9222?timezone=Europe/Minsk",
    )
    monkeypatch.setattr(
        browser_fetcher.settings,
        "browser_fetch_fingerprint_seed",
        "abc",
    )

    browser_fetcher.fetch_html("https://example.test/page")

    assert chromium.cdp_url == "http://c:9222?timezone=Europe/Minsk&fingerprint=abc"


def test_browser_fetcher_closes_resources_when_goto_fails(monkeypatch):
    page = FakePage(error=RuntimeError("blocked"))
    browser, _chromium = install_fake_playwright(monkeypatch, page)

    with pytest.raises(BrowserFetchError):
        browser_fetcher.fetch_html("https://example.test/page")

    assert page.closed is True
    assert browser.closed is True


def test_browser_fetcher_wraps_missing_playwright(monkeypatch):
    monkeypatch.setattr(browser_fetcher, "sync_playwright", None)

    with pytest.raises(BrowserFetchError, match="Playwright is not installed"):
        browser_fetcher.fetch_html("https://example.test/page")
