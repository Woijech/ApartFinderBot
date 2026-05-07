from datetime import UTC, datetime

from kufarpars.bot_storage import BotStorage
from kufarpars.client import SearchRequest


def test_bot_storage_persists_profile_and_seen_ids(tmp_path) -> None:
    storage = BotStorage(str(tmp_path / "bot.sqlite3"))
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


def test_bot_storage_resets_seen_ids(tmp_path) -> None:
    storage = BotStorage(str(tmp_path / "bot.sqlite3"))
    storage.get(123)
    storage.mark_seen(123, [1, 2])

    storage.reset_seen(123)

    assert storage.recent_seen_ids(123) == []


def test_bot_storage_supports_multiple_subscriptions(tmp_path) -> None:
    storage = BotStorage(str(tmp_path / "bot.sqlite3"))
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
