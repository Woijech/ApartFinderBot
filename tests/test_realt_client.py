import httpx
import pytest

from apartmentfinder.infrastructure.browser_fetcher import BrowserFetchError
from apartmentfinder.infrastructure.sources.realt import client as realt_client
from apartmentfinder.infrastructure.sources.realt.client import (
    RealtClient,
    RealtNetworkError,
)

EMPTY_TOTAL_HTML = """
<html><body><main><h1>96 объявлений</h1></main></body></html>
"""


RENDERED_LISTING_HTML = """
<html><body>
  <main>
    <div>451 р./мес.</div>
    <div>≈ 160 $/мес.</div>
    <div>Комната 55 м² 5/9 этаж</div>
    <div>г. Минск, ул. Слободская, 135</div>
    <div>7 часов назад ID 4119308</div>
  </main>
</body></html>
"""


def test_realt_retries_empty_search_result_with_browser(monkeypatch):
    client = RealtClient(retries=0)
    client._client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=EMPTY_TOTAL_HTML, request=request)
        )
    )
    monkeypatch.setattr(realt_client.settings, "browser_fetch_enabled", True)
    monkeypatch.setattr(realt_client.settings, "browser_fetch_fallback_on_empty", True)
    monkeypatch.setattr(
        realt_client.browser_fetcher,
        "fetch_html",
        lambda url: RENDERED_LISTING_HTML,
    )

    result = client.search_page("https://realt.by/rent/room-for-long/", "room")

    assert [listing.ad_id for listing in result.listings] == [4119308]


def test_realt_browser_fallback_failure_preserves_network_error(monkeypatch):
    client = RealtClient(retries=0)
    client._client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(403, request=request)
        )
    )
    monkeypatch.setattr(realt_client.settings, "browser_fetch_enabled", True)

    def fail(_url):
        raise BrowserFetchError("browser unavailable")

    monkeypatch.setattr(realt_client.browser_fetcher, "fetch_html", fail)

    with pytest.raises(RealtNetworkError):
        client.fetch_url("https://realt.by/blocked")
