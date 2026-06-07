"""Storage repository for Telegram bot profiles and seen listings.

The public methods intentionally speak in bot-domain terms while SQLAlchemy
owns the database details underneath. Runtime storage is PostgreSQL-only.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, delete, func, select, text, tuple_
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from apartmentfinder.domain.models import Listing, ListingImage, SearchRequest
from apartmentfinder.infrastructure.persistence.models import (
    BannedSellerRow,
    Base,
    ChatRow,
    ListingHistoryRow,
    NotificationLogRow,
    SeenAdRow,
    SubscriptionRow,
)

DEFAULT_SUBSCRIPTION_TITLE = "Основной поиск"
ListingKey = tuple[str, int]


@dataclass
class SearchSubscription:
    """A saved listing search with monitoring settings and recent seen ids."""

    chat_id: int
    id: int | None = None
    title: str = DEFAULT_SUBSCRIPTION_TITLE
    enabled: bool = False
    watch_started_at: datetime | None = None
    request: SearchRequest = field(default_factory=SearchRequest)
    seen_ids: list[int] = field(default_factory=list)


UserProfile = SearchSubscription


@dataclass(frozen=True)
class BannedSeller:
    """A seller hidden by one Telegram chat."""

    id: int
    chat_id: int
    source: str
    seller_name: str


class BotStorage:
    """Persist bot profiles and seen listing ids through SQLAlchemy."""

    def __init__(
        self,
        database_url: str,
        seen_ttl_days: int = 60,
        max_seen_per_chat: int = 5000,
        create_schema: bool = True,
    ) -> None:
        """Create storage and optionally initialize the database schema."""
        self._database_url = _validate_database_url(database_url)
        self._seen_ttl_days = seen_ttl_days
        self._max_seen_per_chat = max_seen_per_chat
        self._engine = _create_engine(self._database_url)
        if create_schema:
            Base.metadata.create_all(self._engine)
        self._session_factory = sessionmaker(
            bind=self._engine,
            expire_on_commit=False,
            future=True,
        )

    def close(self) -> None:
        """Dispose the underlying SQLAlchemy engine."""
        self._engine.dispose()

    @property
    def engine(self) -> Engine:
        """Return the SQLAlchemy engine for Alembic and diagnostics."""
        return self._engine

    def check_connection(self) -> None:
        """Verify that PostgreSQL is reachable before the bot starts polling."""
        with self._engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    def get(self, chat_id: int) -> UserProfile:
        """Return an existing profile or create a new default one."""
        with self._session_factory() as session:
            subscription = self._default_subscription(session, chat_id)
            session.commit()
            return self._profile_from_subscription(session, subscription)

    def list_subscriptions(self, chat_id: int) -> list[SearchSubscription]:
        """Return all saved searches for one chat."""
        with self._session_factory() as session:
            self._ensure_chat(session, chat_id)
            subscriptions = session.scalars(
                select(SubscriptionRow)
                .where(SubscriptionRow.chat_id == chat_id)
                .order_by(SubscriptionRow.id.asc())
            ).all()
            if not subscriptions:
                subscriptions = [self._default_subscription(session, chat_id)]
                session.commit()
            return [
                self._profile_from_subscription(session, subscription)
                for subscription in subscriptions
            ]

    def create_subscription(
        self,
        chat_id: int,
        title: str,
        request: SearchRequest | None = None,
    ) -> SearchSubscription:
        """Create a saved search for one chat."""
        with self._session_factory() as session:
            self._ensure_chat(session, chat_id)
            subscription = SubscriptionRow(
                chat_id=chat_id,
                title=title,
                enabled=False,
                request_json=_request_to_json(request or SearchRequest()),
            )
            session.add(subscription)
            session.commit()
            return self._profile_from_subscription(session, subscription)

    def get_subscription(
        self,
        chat_id: int,
        subscription_id: int,
    ) -> SearchSubscription:
        """Return one saved search owned by one chat."""
        with self._session_factory() as session:
            subscription = self._subscription_by_id(session, chat_id, subscription_id)
            return self._profile_from_subscription(session, subscription)

    def update_subscription(self, subscription: SearchSubscription) -> None:
        """Persist one saved search."""
        if subscription.id is None:
            self.update(subscription)
            return
        with self._session_factory() as session:
            row = self._subscription_by_id(
                session,
                subscription.chat_id,
                subscription.id,
            )
            row.title = subscription.title
            row.enabled = subscription.enabled
            row.watch_started_at = _datetime_to_db(subscription.watch_started_at)
            row.request_json = _request_to_json(subscription.request)
            row.updated_at = datetime.now(UTC)
            if subscription.seen_ids:
                self._mark_seen_for_subscription(session, row.id, subscription.seen_ids)
            session.commit()

    def delete_subscription(self, chat_id: int, subscription_id: int) -> None:
        """Delete one saved search and its seen-ad rows."""
        with self._session_factory() as session:
            row = self._subscription_by_id(session, chat_id, subscription_id)
            session.delete(row)
            session.commit()

    def update(self, profile: UserProfile) -> None:
        """Upsert profile settings and optionally persist provided seen ids."""
        with self._session_factory() as session:
            subscription = self._default_subscription(session, profile.chat_id)
            subscription.enabled = profile.enabled
            subscription.watch_started_at = _datetime_to_db(profile.watch_started_at)
            subscription.request_json = _request_to_json(profile.request)
            subscription.updated_at = datetime.now(UTC)
            session.flush()
            if profile.seen_ids:
                self._mark_seen_for_subscription(
                    session,
                    subscription.id,
                    profile.seen_ids,
                )
            session.commit()

    def all_enabled(self) -> list[UserProfile]:
        """Return all profiles with background monitoring enabled."""
        with self._session_factory() as session:
            subscriptions = session.scalars(
                select(SubscriptionRow).where(SubscriptionRow.enabled.is_(True))
            ).all()
            return [
                self._profile_from_subscription(session, subscription)
                for subscription in subscriptions
            ]

    def recent_seen_ids(self, chat_id: int, limit: int | None = None) -> list[int]:
        """Return recent seen listing ids for one chat."""
        with self._session_factory() as session:
            subscription = self._default_subscription(session, chat_id)
            rows = session.scalars(
                select(SeenAdRow.ad_id)
                .where(SeenAdRow.subscription_id == subscription.id)
                .where(SeenAdRow.source == "kufar")
                .order_by(SeenAdRow.seen_at.desc())
                .limit(limit or self._max_seen_per_chat)
            ).all()
            return [int(ad_id) for ad_id in rows]

    def recent_seen_items(
        self,
        chat_id: int,
        limit: int | None = None,
    ) -> list[ListingKey]:
        """Return recent source/id pairs for one chat."""
        with self._session_factory() as session:
            subscription = self._default_subscription(session, chat_id)
            rows = session.execute(
                select(SeenAdRow.source, SeenAdRow.ad_id)
                .where(SeenAdRow.subscription_id == subscription.id)
                .order_by(SeenAdRow.seen_at.desc())
                .limit(limit or self._max_seen_per_chat)
            ).all()
            return [(str(source), int(ad_id)) for source, ad_id in rows]

    def recent_seen_items_for_subscription(
        self,
        subscription_id: int,
        limit: int | None = None,
    ) -> list[ListingKey]:
        """Return recent source/id pairs for one saved search."""
        with self._session_factory() as session:
            rows = session.execute(
                select(SeenAdRow.source, SeenAdRow.ad_id)
                .where(SeenAdRow.subscription_id == subscription_id)
                .order_by(SeenAdRow.seen_at.desc())
                .limit(limit or self._max_seen_per_chat)
            ).all()
            return [(str(source), int(ad_id)) for source, ad_id in rows]

    def save_listing_history_for_subscription(
        self,
        subscription_id: int,
        listings: list[Listing],
        limit: int = 50,
    ) -> None:
        """Store recent matching listing snapshots for old-listing browsing."""
        if not listings:
            return
        saved_at = datetime.now(UTC)
        unique_listings = {
            (listing.source, int(listing.ad_id)): listing for listing in listings
        }
        with self._session_factory() as session:
            for (source, ad_id), listing in unique_listings.items():
                row = session.scalar(
                    select(ListingHistoryRow).where(
                        ListingHistoryRow.subscription_id == subscription_id,
                        ListingHistoryRow.source == source,
                        ListingHistoryRow.ad_id == ad_id,
                    )
                )
                listing_json = _listing_to_json(listing)
                if row is None:
                    session.add(
                        ListingHistoryRow(
                            subscription_id=subscription_id,
                            source=source,
                            ad_id=ad_id,
                            seller_name=listing.seller_name,
                            listing_json=listing_json,
                            saved_at=saved_at,
                        )
                    )
                    continue
                row.seller_name = listing.seller_name
                row.listing_json = listing_json
                row.saved_at = saved_at
            self._prune_listing_history_for_subscription(
                session,
                subscription_id,
                limit,
            )
            session.commit()

    def listing_history_count_for_subscription(self, subscription_id: int) -> int:
        """Return stored listing snapshot count for one saved search."""
        with self._session_factory() as session:
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(ListingHistoryRow)
                    .where(ListingHistoryRow.subscription_id == subscription_id)
                )
                or 0
            )

    def history_listing_for_subscription(
        self,
        subscription_id: int,
        index: int,
    ) -> Listing | None:
        """Return one old listing snapshot by newest-first index."""
        with self._session_factory() as session:
            row = session.scalar(
                select(ListingHistoryRow)
                .where(ListingHistoryRow.subscription_id == subscription_id)
                .order_by(ListingHistoryRow.saved_at.desc())
                .offset(max(index, 0))
                .limit(1)
            )
            return _listing_from_json(row.listing_json) if row is not None else None

    def history_listing_by_key(
        self,
        chat_id: int,
        source: str,
        ad_id: int,
    ) -> Listing | None:
        """Return the latest stored listing for one chat/source/ad pair."""
        with self._session_factory() as session:
            row = session.scalar(
                select(ListingHistoryRow)
                .join(SubscriptionRow)
                .where(
                    SubscriptionRow.chat_id == chat_id,
                    ListingHistoryRow.source == source,
                    ListingHistoryRow.ad_id == ad_id,
                )
                .order_by(ListingHistoryRow.saved_at.desc())
                .limit(1)
            )
            return _listing_from_json(row.listing_json) if row is not None else None

    def list_banned_sellers(self, chat_id: int) -> list[BannedSeller]:
        """Return sellers hidden by one chat."""
        with self._session_factory() as session:
            self._ensure_chat(session, chat_id)
            rows = session.scalars(
                select(BannedSellerRow)
                .where(BannedSellerRow.chat_id == chat_id)
                .order_by(BannedSellerRow.created_at.desc())
            ).all()
            return [
                BannedSeller(
                    id=row.id,
                    chat_id=row.chat_id,
                    source=row.source,
                    seller_name=row.seller_name,
                )
                for row in rows
            ]

    def ban_seller(self, chat_id: int, source: str, seller_name: str) -> None:
        """Hide future listings from one seller for a chat."""
        seller_name = seller_name.strip()
        if not seller_name:
            return
        seller_key = _seller_key(seller_name)
        with self._session_factory() as session:
            self._ensure_chat(session, chat_id)
            row = session.scalar(
                select(BannedSellerRow).where(
                    BannedSellerRow.chat_id == chat_id,
                    BannedSellerRow.source == source,
                    BannedSellerRow.seller_key == seller_key,
                )
            )
            if row is None:
                session.add(
                    BannedSellerRow(
                        chat_id=chat_id,
                        source=source,
                        seller_name=seller_name,
                        seller_key=seller_key,
                    )
                )
            else:
                row.seller_name = seller_name
            session.commit()

    def unban_seller(self, chat_id: int, banned_seller_id: int) -> None:
        """Remove one seller from a chat blacklist."""
        with self._session_factory() as session:
            row = session.scalar(
                select(BannedSellerRow).where(
                    BannedSellerRow.chat_id == chat_id,
                    BannedSellerRow.id == banned_seller_id,
                )
            )
            if row is not None:
                session.delete(row)
                session.commit()

    def is_seller_banned(
        self,
        chat_id: int,
        source: str,
        seller_name: str | None,
    ) -> bool:
        """Return whether one listing seller is hidden by the chat."""
        if not seller_name:
            return False
        with self._session_factory() as session:
            return (
                session.scalar(
                    select(BannedSellerRow.id).where(
                        BannedSellerRow.chat_id == chat_id,
                        BannedSellerRow.source == source,
                        BannedSellerRow.seller_key == _seller_key(seller_name),
                    )
                )
                is not None
            )

    def mark_seen(self, chat_id: int, ad_ids: list[int]) -> None:
        """Insert seen listing ids using a unique key to prevent duplicates."""
        if not ad_ids:
            return
        with self._session_factory() as session:
            subscription = self._default_subscription(session, chat_id)
            session.flush()
            self._mark_seen_for_subscription(session, subscription.id, ad_ids)
            self._prune_seen_for_subscription(session, subscription.id)
            session.commit()

    def mark_seen_items(self, chat_id: int, listing_keys: list[ListingKey]) -> None:
        """Insert seen source/id pairs for one chat."""
        if not listing_keys:
            return
        with self._session_factory() as session:
            subscription = self._default_subscription(session, chat_id)
            session.flush()
            self._mark_seen_items_for_subscription(
                session,
                subscription.id,
                listing_keys,
            )
            self._prune_seen_for_subscription(session, subscription.id)
            session.commit()

    def mark_seen_for_subscription(
        self,
        subscription_id: int,
        ad_ids: list[int],
    ) -> None:
        """Insert seen listing ids for one saved search."""
        if not ad_ids:
            return
        with self._session_factory() as session:
            self._mark_seen_for_subscription(session, subscription_id, ad_ids)
            self._prune_seen_for_subscription(session, subscription_id)
            session.commit()

    def mark_seen_items_for_subscription(
        self,
        subscription_id: int,
        listing_keys: list[ListingKey],
    ) -> None:
        """Insert seen source/id pairs for one saved search."""
        if not listing_keys:
            return
        with self._session_factory() as session:
            self._mark_seen_items_for_subscription(
                session,
                subscription_id,
                listing_keys,
            )
            self._prune_seen_for_subscription(session, subscription_id)
            session.commit()

    def unseen_ids(self, chat_id: int, ad_ids: list[int]) -> list[int]:
        """Return ids from ``ad_ids`` that have not been seen for this chat."""
        if not ad_ids:
            return []
        unique_ids = list(dict.fromkeys(int(ad_id) for ad_id in ad_ids))
        with self._session_factory() as session:
            subscription = self._default_subscription(session, chat_id)
            seen_ids = set(
                session.scalars(
                    select(SeenAdRow.ad_id).where(
                        SeenAdRow.subscription_id == subscription.id,
                        SeenAdRow.source == "kufar",
                        SeenAdRow.ad_id.in_(unique_ids),
                    )
                ).all()
            )
            return [ad_id for ad_id in ad_ids if ad_id not in seen_ids]

    def unseen_items(
        self,
        chat_id: int,
        listing_keys: list[ListingKey],
    ) -> list[ListingKey]:
        """Return source/id pairs that have not been seen for this chat."""
        if not listing_keys:
            return []
        unique_keys = _unique_listing_keys(listing_keys)
        with self._session_factory() as session:
            subscription = self._default_subscription(session, chat_id)
            rows = session.execute(
                select(SeenAdRow.source, SeenAdRow.ad_id).where(
                    SeenAdRow.subscription_id == subscription.id,
                    tuple_(SeenAdRow.source, SeenAdRow.ad_id).in_(unique_keys),
                )
            ).all()
            seen_keys = {
                (str(source), int(ad_id))
                for source, ad_id in rows
            }
            return [key for key in listing_keys if key not in seen_keys]

    def unseen_ids_for_subscription(
        self,
        subscription_id: int,
        ad_ids: list[int],
    ) -> list[int]:
        """Return ids from ``ad_ids`` that have not been seen by one saved search."""
        if not ad_ids:
            return []
        unique_ids = list(dict.fromkeys(int(ad_id) for ad_id in ad_ids))
        with self._session_factory() as session:
            seen_ids = set(
                session.scalars(
                    select(SeenAdRow.ad_id).where(
                        SeenAdRow.subscription_id == subscription_id,
                        SeenAdRow.source == "kufar",
                        SeenAdRow.ad_id.in_(unique_ids),
                    )
                ).all()
            )
            return [ad_id for ad_id in ad_ids if ad_id not in seen_ids]

    def unseen_items_for_subscription(
        self,
        subscription_id: int,
        listing_keys: list[ListingKey],
    ) -> list[ListingKey]:
        """Return source/id pairs not seen by one saved search."""
        if not listing_keys:
            return []
        unique_keys = _unique_listing_keys(listing_keys)
        with self._session_factory() as session:
            rows = session.execute(
                select(SeenAdRow.source, SeenAdRow.ad_id).where(
                    SeenAdRow.subscription_id == subscription_id,
                    tuple_(SeenAdRow.source, SeenAdRow.ad_id).in_(unique_keys),
                )
            ).all()
            seen_keys = {
                (str(source), int(ad_id))
                for source, ad_id in rows
            }
            return [key for key in listing_keys if key not in seen_keys]

    def reset_seen(self, chat_id: int) -> None:
        """Delete seen listing ids for one chat, usually after filter changes."""
        with self._session_factory() as session:
            subscription = self._default_subscription(session, chat_id)
            session.execute(
                delete(SeenAdRow).where(SeenAdRow.subscription_id == subscription.id)
            )
            session.commit()

    def reset_seen_for_subscription(self, subscription_id: int) -> None:
        """Delete seen listing ids for one saved search."""
        with self._session_factory() as session:
            session.execute(
                delete(SeenAdRow).where(SeenAdRow.subscription_id == subscription_id)
            )
            session.commit()

    def prune_seen(self, chat_id: int | None = None) -> None:
        """Remove old and excessive seen-id rows to keep storage small."""
        cutoff = datetime.now(UTC) - timedelta(days=self._seen_ttl_days)
        with self._session_factory() as session:
            if chat_id is not None:
                subscription = self._default_subscription(session, chat_id)
                self._prune_seen_for_subscription(session, subscription.id, cutoff)
            else:
                session.execute(delete(SeenAdRow).where(SeenAdRow.seen_at < cutoff))
            session.commit()

    def log_notification(
        self,
        chat_id: int,
        ad_id: int,
        status: str,
        error: str | None = None,
        source: str = "kufar",
    ) -> None:
        """Persist one notification attempt for later diagnostics."""
        with self._session_factory() as session:
            subscription = self._default_subscription(session, chat_id)
            session.add(
                NotificationLogRow(
                    subscription_id=subscription.id,
                    ad_id=ad_id,
                    source=source,
                    status=status,
                    error=error,
                )
            )
            session.commit()

    def log_notification_for_subscription(
        self,
        subscription_id: int,
        ad_id: int,
        status: str,
        error: str | None = None,
        source: str = "kufar",
    ) -> None:
        """Persist one notification attempt for a saved search."""
        with self._session_factory() as session:
            session.add(
                NotificationLogRow(
                    subscription_id=subscription_id,
                    ad_id=ad_id,
                    source=source,
                    status=status,
                    error=error,
                )
            )
            session.commit()

    def _default_subscription(
        self,
        session: Session,
        chat_id: int,
    ) -> SubscriptionRow:
        """Return the default subscription row for the current bot UI."""
        self._ensure_chat(session, chat_id)

        subscription = session.scalar(
            select(SubscriptionRow).where(
                SubscriptionRow.chat_id == chat_id,
                SubscriptionRow.title == DEFAULT_SUBSCRIPTION_TITLE,
            )
        )
        if subscription is not None:
            return subscription

        subscription = SubscriptionRow(
            chat_id=chat_id,
            title=DEFAULT_SUBSCRIPTION_TITLE,
            enabled=False,
            request_json=_request_to_json(SearchRequest()),
        )
        session.add(subscription)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            subscription = session.scalar(
                select(SubscriptionRow).where(
                    SubscriptionRow.chat_id == chat_id,
                    SubscriptionRow.title == DEFAULT_SUBSCRIPTION_TITLE,
                )
            )
            if subscription is None:
                raise
        return subscription

    def _ensure_chat(self, session: Session, chat_id: int) -> ChatRow:
        """Return an existing chat row or create it."""
        chat = session.get(ChatRow, chat_id)
        if chat is not None:
            return chat
        chat = ChatRow(id=chat_id)
        session.add(chat)
        session.flush()
        return chat

    def _subscription_by_id(
        self,
        session: Session,
        chat_id: int,
        subscription_id: int,
    ) -> SubscriptionRow:
        """Fetch one subscription and ensure it belongs to ``chat_id``."""
        subscription = session.scalar(
            select(SubscriptionRow).where(
                SubscriptionRow.id == subscription_id,
                SubscriptionRow.chat_id == chat_id,
            )
        )
        if subscription is None:
            raise ValueError(f"Unknown subscription: {subscription_id}")
        return subscription

    def _profile_from_subscription(
        self,
        session: Session,
        subscription: SubscriptionRow,
    ) -> SearchSubscription:
        """Convert one subscription row into the current bot profile object."""
        seen_ids = session.scalars(
            select(SeenAdRow.ad_id)
            .where(SeenAdRow.subscription_id == subscription.id)
            .where(SeenAdRow.source == "kufar")
            .order_by(SeenAdRow.seen_at.desc())
            .limit(self._max_seen_per_chat)
        ).all()
        return SearchSubscription(
            chat_id=subscription.chat_id,
            id=subscription.id,
            title=subscription.title,
            enabled=subscription.enabled,
            watch_started_at=_datetime_from_db(subscription.watch_started_at),
            request=_request_from_json(subscription.request_json),
            seen_ids=[int(ad_id) for ad_id in seen_ids],
        )

    def _mark_seen_for_subscription(
        self,
        session: Session,
        subscription_id: int,
        ad_ids: list[int],
    ) -> None:
        """Insert or refresh default-source seen ids for one subscription."""
        self._mark_seen_items_for_subscription(
            session,
            subscription_id,
            [("kufar", ad_id) for ad_id in ad_ids],
        )

    def _mark_seen_items_for_subscription(
        self,
        session: Session,
        subscription_id: int,
        listing_keys: list[ListingKey],
    ) -> None:
        """Insert or refresh seen listing ids for one subscription."""
        seen_at = datetime.now(UTC)
        unique_keys = _unique_listing_keys(listing_keys)
        existing_rows = session.execute(
            select(SeenAdRow.source, SeenAdRow.ad_id).where(
                SeenAdRow.subscription_id == subscription_id,
                tuple_(SeenAdRow.source, SeenAdRow.ad_id).in_(unique_keys),
            )
        ).all()
        existing = {
            (str(source), int(ad_id))
            for source, ad_id in existing_rows
        }
        for source, ad_id in unique_keys:
            if (source, ad_id) in existing:
                session.execute(
                    SeenAdRow.__table__.update()
                    .where(
                        SeenAdRow.subscription_id == subscription_id,
                        SeenAdRow.source == source,
                        SeenAdRow.ad_id == ad_id,
                    )
                    .values(seen_at=seen_at)
                )
                continue
            session.add(
                SeenAdRow(
                    subscription_id=subscription_id,
                    ad_id=ad_id,
                    source=source,
                    seen_at=seen_at,
                )
            )

    def _prune_seen_for_subscription(
        self,
        session: Session,
        subscription_id: int,
        cutoff: datetime | None = None,
    ) -> None:
        """Prune one subscription's seen listings by age and count."""
        cutoff = cutoff or datetime.now(UTC) - timedelta(days=self._seen_ttl_days)
        session.execute(
            delete(SeenAdRow).where(
                SeenAdRow.subscription_id == subscription_id,
                SeenAdRow.seen_at < cutoff,
            )
        )
        keep_rows = session.execute(
            select(SeenAdRow.source, SeenAdRow.ad_id)
            .where(SeenAdRow.subscription_id == subscription_id)
            .order_by(SeenAdRow.seen_at.desc())
            .limit(self._max_seen_per_chat)
        ).all()
        keep_keys = [(str(source), int(ad_id)) for source, ad_id in keep_rows]
        if keep_keys:
            session.execute(
                delete(SeenAdRow).where(
                    SeenAdRow.subscription_id == subscription_id,
                    tuple_(SeenAdRow.source, SeenAdRow.ad_id).not_in(keep_keys),
                )
            )

    def _prune_listing_history_for_subscription(
        self,
        session: Session,
        subscription_id: int,
        limit: int,
    ) -> None:
        """Keep only the newest stored listing snapshots for one search."""
        stale_ids = session.scalars(
            select(ListingHistoryRow.id)
            .where(ListingHistoryRow.subscription_id == subscription_id)
            .order_by(ListingHistoryRow.saved_at.desc())
            .offset(max(limit, 0))
        ).all()
        if stale_ids:
            session.execute(
                delete(ListingHistoryRow).where(ListingHistoryRow.id.in_(stale_ids))
            )


def _create_engine(database_url: str) -> Engine:
    """Create a SQLAlchemy engine for PostgreSQL."""
    return create_engine(database_url, future=True)


def _validate_database_url(value: str) -> str:
    """Reject non-PostgreSQL storage URLs."""
    if not value.startswith(("postgresql://", "postgresql+psycopg://")):
        raise ValueError("BotStorage requires a PostgreSQL database URL")
    return value


def _request_to_json(request: SearchRequest) -> str:
    """Serialize SearchRequest as compact JSON."""
    return json.dumps(asdict(request), ensure_ascii=False, separators=(",", ":"))


def _request_from_json(value: str) -> SearchRequest:
    """Deserialize SearchRequest from JSON stored in the database."""
    data = json.loads(value)
    return SearchRequest(**data)


def _listing_to_json(listing: Listing) -> str:
    """Serialize Listing as compact JSON for history snapshots."""
    data = asdict(listing)
    if listing.published_at is not None:
        data["published_at"] = listing.published_at.isoformat()
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)


def _listing_from_json(value: str) -> Listing:
    """Deserialize Listing from history snapshot JSON."""
    data = json.loads(value)
    published_at = _datetime_from_db(data.get("published_at"))
    images = [
        ListingImage(
            gallery_url=str(image["gallery_url"]),
            thumbnail_url=image.get("thumbnail_url"),
        )
        for image in data.get("images", [])
        if image.get("gallery_url")
    ]
    return Listing(
        ad_id=int(data["ad_id"]),
        title=str(data.get("title") or ""),
        url=str(data.get("url") or ""),
        source=str(data.get("source") or "kufar"),
        price_byn=data.get("price_byn"),
        price_usd=data.get("price_usd"),
        currency=data.get("currency"),
        address=data.get("address"),
        rooms=data.get("rooms"),
        area_m2=data.get("area_m2"),
        floor=data.get("floor"),
        total_floors=data.get("total_floors"),
        metro=list(data.get("metro") or []),
        description=data.get("description"),
        published_at=published_at,
        seller_name=data.get("seller_name"),
        company_ad=bool(data.get("company_ad")),
        images=images,
        raw_parameters=dict(data.get("raw_parameters") or {}),
    )


def _unique_listing_keys(listing_keys: list[ListingKey]) -> list[ListingKey]:
    """Normalize and deduplicate source/id pairs while preserving order."""
    return list(
        dict.fromkeys((str(source), int(ad_id)) for source, ad_id in listing_keys)
    )


def _seller_key(seller_name: str) -> str:
    """Normalize seller names for blacklist matching."""
    return " ".join(seller_name.casefold().split())


def _datetime_to_db(value: datetime | None) -> datetime | None:
    """Normalize optional datetimes for database storage."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _datetime_from_db(value: datetime | str | None) -> datetime | None:
    """Deserialize optional datetimes from database storage."""
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
