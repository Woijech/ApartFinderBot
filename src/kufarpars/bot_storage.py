"""Storage repository for Telegram bot profiles and seen listings.

The public methods intentionally speak in bot-domain terms while SQLAlchemy
owns the database details underneath. SQLite remains the local default, and the
same models work with PostgreSQL through ``KUFARPARS_DATABASE_URL``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from kufarpars.client import SearchRequest
from kufarpars.db import Base, ChatRow, NotificationLogRow, SeenAdRow, SubscriptionRow

DEFAULT_SUBSCRIPTION_TITLE = "Основной поиск"


@dataclass
class SearchSubscription:
    """A saved Kufar search with monitoring settings and recent seen listing ids."""

    chat_id: int
    id: int | None = None
    title: str = DEFAULT_SUBSCRIPTION_TITLE
    enabled: bool = False
    watch_started_at: datetime | None = None
    request: SearchRequest = field(default_factory=SearchRequest)
    seen_ids: list[int] = field(default_factory=list)


UserProfile = SearchSubscription


class BotStorage:
    """Persist bot profiles and seen listing ids through SQLAlchemy."""

    def __init__(
        self,
        database_url: str,
        legacy_json_path: str | None = None,
        seen_ttl_days: int = 60,
        max_seen_per_chat: int = 5000,
        create_schema: bool = True,
    ) -> None:
        """Create storage, initialize schema, and migrate old JSON state if found."""
        self._database_url = _normalize_database_url(database_url)
        self._legacy_json_path = Path(legacy_json_path) if legacy_json_path else None
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
        self._migrate_legacy_json()

    def close(self) -> None:
        """Dispose the underlying SQLAlchemy engine."""
        self._engine.dispose()

    @property
    def engine(self) -> Engine:
        """Return the SQLAlchemy engine for Alembic and diagnostics."""
        return self._engine

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
                .order_by(SeenAdRow.seen_at.desc())
                .limit(limit or self._max_seen_per_chat)
            ).all()
            return [int(ad_id) for ad_id in rows]

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
                        SeenAdRow.ad_id.in_(unique_ids),
                    )
                ).all()
            )
            return [ad_id for ad_id in ad_ids if ad_id not in seen_ids]

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
                        SeenAdRow.ad_id.in_(unique_ids),
                    )
                ).all()
            )
            return [ad_id for ad_id in ad_ids if ad_id not in seen_ids]

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
    ) -> None:
        """Persist one notification attempt for later diagnostics."""
        with self._session_factory() as session:
            subscription = self._default_subscription(session, chat_id)
            session.add(
                NotificationLogRow(
                    subscription_id=subscription.id,
                    ad_id=ad_id,
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
        """Return the default subscription row for compatibility with current bot UI."""
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
        """Insert or refresh seen listing ids for one subscription."""
        seen_at = datetime.now(UTC)
        unique_ids = list(dict.fromkeys(int(ad_id) for ad_id in ad_ids))
        existing = set(
            session.scalars(
                select(SeenAdRow.ad_id).where(
                    SeenAdRow.subscription_id == subscription_id,
                    SeenAdRow.ad_id.in_(unique_ids),
                )
            ).all()
        )
        for ad_id in unique_ids:
            if ad_id in existing:
                session.execute(
                    SeenAdRow.__table__.update()
                    .where(
                        SeenAdRow.subscription_id == subscription_id,
                        SeenAdRow.ad_id == ad_id,
                    )
                    .values(seen_at=seen_at)
                )
                continue
            session.add(
                SeenAdRow(
                    subscription_id=subscription_id,
                    ad_id=ad_id,
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
        keep_ids = session.scalars(
            select(SeenAdRow.ad_id)
            .where(SeenAdRow.subscription_id == subscription_id)
            .order_by(SeenAdRow.seen_at.desc())
            .limit(self._max_seen_per_chat)
        ).all()
        if keep_ids:
            session.execute(
                delete(SeenAdRow).where(
                    SeenAdRow.subscription_id == subscription_id,
                    SeenAdRow.ad_id.not_in(keep_ids),
                )
            )

    def _migrate_legacy_json(self) -> None:
        """Import profiles from the previous JSON storage file once."""
        if self._legacy_json_path is None or not self._legacy_json_path.exists():
            return
        marker_path = Path(str(self._legacy_json_path) + ".sqlalchemy_migrated")
        if marker_path.exists():
            return
        data = json.loads(self._legacy_json_path.read_text(encoding="utf-8"))
        for item in (data.get("profiles") or {}).values():
            profile = _profile_from_legacy_dict(item)
            self.update(profile)
        marker_path.write_text(_now_iso(), encoding="utf-8")


def _create_engine(database_url: str) -> Engine:
    """Create a SQLAlchemy engine with SQLite-friendly defaults."""
    connect_args = (
        {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    )
    return create_engine(database_url, connect_args=connect_args, future=True)


def _normalize_database_url(value: str) -> str:
    """Accept full SQLAlchemy URLs and legacy filesystem paths."""
    if "://" in value:
        return value
    return f"sqlite:///{value}"


def _request_to_json(request: SearchRequest) -> str:
    """Serialize SearchRequest as compact JSON."""
    return json.dumps(asdict(request), ensure_ascii=False, separators=(",", ":"))


def _request_from_json(value: str) -> SearchRequest:
    """Deserialize SearchRequest from JSON stored in the database."""
    data = json.loads(value)
    return SearchRequest(**data)


def _profile_from_legacy_dict(data: dict[str, object]) -> UserProfile:
    """Deserialize a profile from the old JSON file format."""
    request_data = data.get("request") or {}
    if not isinstance(request_data, dict):
        request_data = {}
    return UserProfile(
        chat_id=int(data["chat_id"]),
        enabled=bool(data.get("enabled")),
        watch_started_at=None,
        request=SearchRequest(**request_data),
        seen_ids=[int(item) for item in data.get("seen_ids", [])],
    )


def _now_iso() -> str:
    """Return a timezone-aware timestamp for marker files."""
    return datetime.now(UTC).isoformat()


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
