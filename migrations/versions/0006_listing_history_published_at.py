"""Order listing history by listing publication time.

Revision ID: 0006_listing_history_published_at
Revises: 0005_favorite_listings
Create Date: 2026-06-07 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_listing_history_published_at"
down_revision = "0005_favorite_listings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add publication timestamps to stored history snapshots."""
    op.add_column(
        "listing_history",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE listing_history
        SET published_at = (listing_json::jsonb ->> 'published_at')::timestamptz
        WHERE listing_json::jsonb ? 'published_at'
          AND listing_json::jsonb ->> 'published_at' IS NOT NULL
          AND listing_json::jsonb ->> 'published_at' != ''
        """
    )
    op.create_index(
        "idx_listing_history_subscription_published_at",
        "listing_history",
        ["subscription_id", "published_at"],
        unique=False,
    )


def downgrade() -> None:
    """Remove publication timestamps from history snapshots."""
    op.drop_index(
        "idx_listing_history_subscription_published_at",
        table_name="listing_history",
    )
    op.drop_column("listing_history", "published_at")
