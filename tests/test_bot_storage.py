import os
from datetime import UTC, datetime

import pytest

from apartmentfinder.domain.models import Listing, SearchRequest
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


def test_bot_storage_orders_history_by_listing_publication_time() -> None:
    storage = make_storage()
    subscription = storage.create_subscription(123, "Комната", SearchRequest())
    old_listing = Listing(
        ad_id=1,
        title="Майское",
        url="https://example.test/1",
        published_at=datetime(2026, 5, 20, 12, 0, tzinfo=UTC),
    )
    new_listing = Listing(
        ad_id=2,
        title="Июньское",
        url="https://example.test/2",
        published_at=datetime(2026, 6, 7, 12, 0, tzinfo=UTC),
    )

    storage.save_listing_history_for_subscription(
        subscription.id,
        [old_listing, new_listing],
    )

    assert storage.history_listing_for_subscription(subscription.id, 0) == new_listing
    assert storage.history_listing_for_subscription(subscription.id, 1) == old_listing


def test_bot_storage_replaces_listing_history_snapshot() -> None:
    storage = make_storage()
    subscription = storage.create_subscription(123, "Комната", SearchRequest())
    old_listing = Listing(
        ad_id=1,
        title="Старое",
        url="https://example.test/1",
        published_at=datetime(2026, 5, 20, 12, 0, tzinfo=UTC),
    )
    current_listing = Listing(
        ad_id=2,
        title="Актуальное",
        url="https://example.test/2",
        published_at=datetime(2026, 6, 7, 12, 0, tzinfo=UTC),
    )

    storage.save_listing_history_for_subscription(
        subscription.id,
        [old_listing, current_listing],
    )
    storage.save_listing_history_for_subscription(subscription.id, [current_listing])

    assert storage.listing_history_count_for_subscription(subscription.id) == 1
    assert (
        storage.history_listing_for_subscription(subscription.id, 0)
        == current_listing
    )
