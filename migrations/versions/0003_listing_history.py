"""Store recent listing snapshots.

Revision ID: 0003_listing_history
Revises: 0002_source_aware_seen_ads
Create Date: 2026-06-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_listing_history"
down_revision: str | None = "0002_source_aware_seen_ads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create listing history snapshots for old-listing browsing."""
    op.create_table(
        "listing_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("subscription_id", sa.Integer(), nullable=False),
        sa.Column("ad_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("seller_name", sa.String(length=240), nullable=True),
        sa.Column("listing_json", sa.Text(), nullable=False),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            ["subscriptions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "subscription_id",
            "source",
            "ad_id",
            name="uq_listing_history_subscription_source_ad",
        ),
    )
    op.create_index(
        "idx_listing_history_subscription_saved_at",
        "listing_history",
        ["subscription_id", "saved_at"],
    )


def downgrade() -> None:
    """Drop listing history snapshots."""
    op.drop_index(
        "idx_listing_history_subscription_saved_at",
        table_name="listing_history",
    )
    op.drop_table("listing_history")
