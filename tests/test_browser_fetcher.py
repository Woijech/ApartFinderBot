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
        self.launch_args = None

    def new_page(self):
        return self.page

    def close(self):
        self.closed = True


def install_fake_cloakbrowser(monkeypatch, page):
    browser = FakeBrowser(page)

    def fake_launch(**kwargs):
        browser.launch_args = kwargs
        return browser

    monkeypatch.setattr(browser_fetcher, "launch", fake_launch)
    return browser


def test_browser_fetcher_fetches_html_with_configured_goto_options(monkeypatch):
    page = FakePage("<html>rendered</html>")
    browser = install_fake_cloakbrowser(monkeypatch, page)
    monkeypatch.setattr(browser_fetcher.settings, "browser_fetch_timeout_seconds", 12)
    monkeypatch.setattr(browser_fetcher.settings, "browser_fetch_wait_until", "load")

    html = browser_fetcher.fetch_html("https://example.test/page")

    assert html == "<html>rendered</html>"
    assert browser.launch_args == {"headless": True}
    assert page.goto_args == {
        "url": "https://example.test/page",
        "wait_until": "load",
        "timeout": 12000,
    }
    assert page.closed is True
    assert browser.closed is True


def test_browser_fetcher_closes_resources_when_goto_fails(monkeypatch):
    page = FakePage(error=RuntimeError("blocked"))
    browser = install_fake_cloakbrowser(monkeypatch, page)

    with pytest.raises(BrowserFetchError):
        browser_fetcher.fetch_html("https://example.test/page")

    assert page.closed is True
    assert browser.closed is True


def test_browser_fetcher_wraps_missing_cloakbrowser(monkeypatch):
    monkeypatch.setattr(browser_fetcher, "launch", None)

    with pytest.raises(BrowserFetchError, match="CloakBrowser is not installed"):
        browser_fetcher.fetch_html("https://example.test/page")
