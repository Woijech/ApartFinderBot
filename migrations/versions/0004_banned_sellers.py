"""Store seller blacklist per chat.

Revision ID: 0004_banned_sellers
Revises: 0003_listing_history
Create Date: 2026-06-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_banned_sellers"
down_revision: str | None = "0003_listing_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create per-chat seller blacklist."""
    op.create_table(
        "banned_sellers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("seller_name", sa.String(length=240), nullable=False),
        sa.Column("seller_key", sa.String(length=260), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "chat_id",
            "source",
            "seller_key",
            name="uq_banned_sellers_chat_source_seller",
        ),
    )


def downgrade() -> None:
    """Drop seller blacklist."""
    op.drop_table("banned_sellers")
