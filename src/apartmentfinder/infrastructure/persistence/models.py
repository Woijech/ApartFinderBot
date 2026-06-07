"""SQLAlchemy database models for bot persistence.

The schema is intentionally small but shaped for growth: a chat can own many
    subscriptions, every subscription has its own seen-ad set, and notification
attempts and listing snapshots can be audited later without changing Telegram
handler code.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""


class ChatRow(Base):
    """A Telegram chat that can own one or more search subscriptions."""

    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    subscriptions: Mapped[list[SubscriptionRow]] = relationship(
        back_populates="chat",
        cascade="all, delete-orphan",
    )
    banned_sellers: Mapped[list[BannedSellerRow]] = relationship(
        back_populates="chat",
        cascade="all, delete-orphan",
    )


class SubscriptionRow(Base):
    """One saved listing search for one Telegram chat."""

    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint("chat_id", "title", name="uq_subscriptions_chat_title"),
        Index("idx_subscriptions_enabled", "enabled"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    watch_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    chat: Mapped[ChatRow] = relationship(back_populates="subscriptions")
    seen_ads: Mapped[list[SeenAdRow]] = relationship(
        back_populates="subscription",
        cascade="all, delete-orphan",
    )
    notification_logs: Mapped[list[NotificationLogRow]] = relationship(
        back_populates="subscription",
        cascade="all, delete-orphan",
    )
    listing_history: Mapped[list[ListingHistoryRow]] = relationship(
        back_populates="subscription",
        cascade="all, delete-orphan",
    )

class SeenAdRow(Base):
    """One listing already seen by one subscription."""

    __tablename__ = "seen_ads"
    __table_args__ = (
        Index("idx_seen_ads_subscription_seen_at", "subscription_id", "seen_at"),
    )

    subscription_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("subscriptions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    ad_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str] = mapped_column(
        String(40),
        primary_key=True,
        default="kufar",
        nullable=False,
    )
    seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    subscription: Mapped[SubscriptionRow] = relationship(back_populates="seen_ads")


class NotificationLogRow(Base):
    """Audit row for a listing notification attempt."""

    __tablename__ = "notification_logs"
    __table_args__ = (
        Index(
            "idx_notification_logs_subscription_sent_at",
            "subscription_id",
            "sent_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subscription_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("subscriptions.id", ondelete="CASCADE"),
        nullable=False,
    )
    ad_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source: Mapped[str] = mapped_column(String(40), default="kufar", nullable=False)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)

    subscription: Mapped[SubscriptionRow] = relationship(
        back_populates="notification_logs"
    )


class ListingHistoryRow(Base):
    """Recent listing snapshot that matched one saved search."""

    __tablename__ = "listing_history"
    __table_args__ = (
        UniqueConstraint(
            "subscription_id",
            "source",
            "ad_id",
            name="uq_listing_history_subscription_source_ad",
        ),
        Index(
            "idx_listing_history_subscription_saved_at",
            "subscription_id",
            "saved_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subscription_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("subscriptions.id", ondelete="CASCADE"),
        nullable=False,
    )
    ad_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    seller_name: Mapped[str | None] = mapped_column(String(240))
    listing_json: Mapped[str] = mapped_column(Text, nullable=False)
    saved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    subscription: Mapped[SubscriptionRow] = relationship(
        back_populates="listing_history"
    )


class BannedSellerRow(Base):
    """One seller hidden by a Telegram chat."""

    __tablename__ = "banned_sellers"
    __table_args__ = (
        UniqueConstraint(
            "chat_id",
            "source",
            "seller_key",
            name="uq_banned_sellers_chat_source_seller",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    seller_name: Mapped[str] = mapped_column(String(240), nullable=False)
    seller_key: Mapped[str] = mapped_column(String(260), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    chat: Mapped[ChatRow] = relationship(back_populates="banned_sellers")
