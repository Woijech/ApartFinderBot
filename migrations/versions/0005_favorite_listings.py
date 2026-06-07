"""Add favorite listing snapshots.

Revision ID: 0005_favorite_listings
Revises: 0004_banned_sellers
Create Date: 2026-06-07 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_favorite_listings"
down_revision = "0004_banned_sellers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create favorite listing storage."""
    op.create_table(
        "favorite_listings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("ad_id", sa.BigInteger(), nullable=False),
        sa.Column("seller_name", sa.String(length=240), nullable=True),
        sa.Column("listing_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "chat_id",
            "source",
            "ad_id",
            name="uq_favorite_listings_chat_source_ad",
        ),
    )
    op.create_index(
        "idx_favorite_listings_chat_created_at",
        "favorite_listings",
        ["chat_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Drop favorite listing storage."""
    op.drop_index(
        "idx_favorite_listings_chat_created_at",
        table_name="favorite_listings",
    )
    op.drop_table("favorite_listings")
