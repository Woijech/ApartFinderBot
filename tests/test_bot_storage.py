import os
from datetime import UTC, datetime

import pytest

from apartmentfinder.domain.models import SearchRequest
from apartmentfinder.infrastructure.persistence.models import Base
from apartmentfinder.infrastructure.persistence.storage import BotStorage

pytestmark = pytest.mark.skipif(
    not os.getenv("APARTMENTFINDER_TEST_DATABASE_URL"),
    reason="PostgreSQL storage tests need APARTMENTFINDER_TEST_DATABASE_URL",
)


def make_storage() -> BotStorage:
    """Create a clean PostgreSQL-backed storage repository for one test."""
    storage = BotStorage(os.environ["APARTMENTFINDER_TEST_DATABASE_URL"])
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


def test_bot_storage_tracks_seen_items_by_source() -> None:
    storage = make_storage()
    subscription = storage.create_subscription(
        123,
        "Комната",
        SearchRequest(property_type="room"),
    )

    storage.mark_seen_items_for_subscription(
        subscription.id,
        [("kufar", 1), ("realt", 1)],
    )

    assert storage.unseen_items_for_subscription(
        subscription.id,
        [("kufar", 1), ("realt", 1), ("realt", 2)],
    ) == [("realt", 2)]
    assert set(storage.recent_seen_items(123)) == {("kufar", 1), ("realt", 1)}
    assert set(storage.recent_seen_items_for_subscription(subscription.id)) == {
        ("kufar", 1),
        ("realt", 1),
    }
