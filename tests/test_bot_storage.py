import os
from datetime import UTC, datetime

import pytest

from kufarpars.bot_storage import BotStorage
from kufarpars.client import SearchRequest
from kufarpars.db import Base

pytestmark = pytest.mark.skipif(
    not os.getenv("KUFARPARS_TEST_DATABASE_URL"),
    reason="PostgreSQL storage tests need KUFARPARS_TEST_DATABASE_URL",
)


def make_storage() -> BotStorage:
    """Create a clean PostgreSQL-backed storage repository for one test."""
    storage = BotStorage(os.environ["KUFARPARS_TEST_DATABASE_URL"])
    Base.metadata.drop_all(storage.engine)
    Base.metadata.create_all(storage.engine)
    return storage


def test_bot_storage_persists_profile_and_seen_ids() -> None:
    storage = make_storage()
    profile = storage.get(123)
    profile.enabled = True
    profile.watch_started_at = datetime(2026, 5, 7, 12, 0, tzinfo=UTC)
    profile.request = SearchRequest(property_type="room", max_price=250)
    storage.update(profile)
    storage.mark_seen(123, [1, 2, 2, 3])

    restored = storage.get(123)

    assert restored.enabled is True
    assert restored.watch_started_at == datetime(2026, 5, 7, 12, 0, tzinfo=UTC)
    assert restored.request.property_type == "room"
    assert restored.request.max_price == 250
    assert set(restored.seen_ids) == {1, 2, 3}
    assert storage.unseen_ids(123, [2, 3, 4]) == [4]


def test_bot_storage_resets_seen_ids() -> None:
    storage = make_storage()
    storage.get(123)
    storage.mark_seen(123, [1, 2])

    storage.reset_seen(123)

    assert storage.recent_seen_ids(123) == []


def test_bot_storage_supports_multiple_subscriptions() -> None:
    storage = make_storage()
    first = storage.create_subscription(
        123,
        "Комната",
        SearchRequest(property_type="room", max_price=250),
    )
    second = storage.create_subscription(
        123,
        "Квартира",
        SearchRequest(property_type="apartment", max_price=500),
    )

    storage.mark_seen_for_subscription(first.id, [1, 2])
    storage.mark_seen_for_subscription(second.id, [2, 3])

    subscriptions = storage.list_subscriptions(123)

    assert [item.title for item in subscriptions] == ["Комната", "Квартира"]
    assert storage.unseen_ids_for_subscription(first.id, [1, 3]) == [3]
    assert storage.unseen_ids_for_subscription(second.id, [1, 3]) == [1]
