import asyncio

import httpx
import pytest

from apartmentfinder.domain.models import SearchRequest
from apartmentfinder.infrastructure.browser_fetcher import BrowserFetchError
from apartmentfinder.infrastructure.config import SourceLimitSettings
from apartmentfinder.infrastructure.sources.kufar import client as kufar_client
from apartmentfinder.infrastructure.sources.kufar.client import (
    KufarClient,
    KufarNetworkError,
    kufar_params,
    kufar_path,
)


def test_search_request_builds_rent_url_parts() -> None:
    request = SearchRequest(rooms=2, max_price=500, text="возле метро")

    assert kufar_path(request) == "/l/minsk/snyat/kvartiru/2k"
    assert kufar_params(request)["prc"] == "r:0,500"
    assert kufar_params(request)["query"] == "возле метро"


def test_search_request_builds_room_url_parts() -> None:
    request = SearchRequest(property_type="room", rooms=2)

    assert kufar_path(request) == "/l/minsk/snyat/komnatu"


def test_search_request_keeps_local_only_filters_out_of_kufar_params() -> None:
    request = SearchRequest(
        district="Центральный",
        metro="Немига",
        include_keywords=["без хозяев"],
        exclude_keywords=["койко-место"],
    )

    params = kufar_params(request)

    assert "district" not in params
    assert "metro" not in params
    assert "include_keywords" not in params
    assert "exclude_keywords" not in params


def test_client_wraps_forbidden_status_as_network_error(monkeypatch) -> None:
    client = KufarClient(retries=0)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(403, request=request)
        )
    )
    monkeypatch.setattr(kufar_client.settings, "browser_fetch_enabled", False)

    with pytest.raises(KufarNetworkError):
        asyncio.run(client.fetch_url("https://example.test/status/403"))
    asyncio.run(client.close())


def test_client_uses_browser_fallback_after_forbidden_status(monkeypatch) -> None:
    client = KufarClient(retries=0)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(403, request=request)
        )
    )
    monkeypatch.setattr(kufar_client.settings, "browser_fetch_enabled", True)

    async def fetch_with_browser(_url: str) -> str:
        return "<html>rendered</html>"

    monkeypatch.setattr(client, "fetch_url_with_browser", fetch_with_browser)

    assert (
        asyncio.run(client.fetch_url("https://example.test/status/403"))
        == "<html>rendered</html>"
    )
    asyncio.run(client.close())


def test_client_browser_fallback_failure_preserves_network_error(monkeypatch) -> None:
    client = KufarClient(retries=0)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(403, request=request)
        )
    )
    monkeypatch.setattr(kufar_client.settings, "browser_fetch_enabled", True)

    async def fail(_url):
        raise BrowserFetchError("browser unavailable")

    monkeypatch.setattr(client, "fetch_url_with_browser", fail)

    with pytest.raises(KufarNetworkError):
        asyncio.run(client.fetch_url("https://example.test/status/403"))
    asyncio.run(client.close())


def test_client_skips_browser_fallback_when_limited(monkeypatch, caplog) -> None:
    client = KufarClient(retries=0)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(403, request=request)
        )
    )
    monkeypatch.setattr(kufar_client.settings, "browser_fetch_enabled", True)
    monkeypatch.setattr(
        kufar_client.settings,
        "source_limits",
        {
            "kufar": SourceLimitSettings(
                min_delay=0,
                jitter=0,
                browser_fallback_limit=0,
            )
        },
    )

    with caplog.at_level("WARNING", logger="apartmentfinder.infrastructure"):
        with pytest.raises(KufarNetworkError):
            asyncio.run(client.fetch_url("https://example.test/status/403"))

    assert "source_browser_fallback_skipped source=kufar limit=0" in caplog.text
    asyncio.run(client.close())
