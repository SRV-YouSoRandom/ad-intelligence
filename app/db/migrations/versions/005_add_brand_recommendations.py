"""Add brand_recommendations table to cache generated strategy reports.

Revision ID: 005
Revises: 004
Create Date: 2026-03-15
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "brand_recommendations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "brand_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("brands.id", ondelete="CASCADE"),
            unique=True,   # one cached report per brand
            nullable=False,
        ),
        sa.Column("brand_context", sa.Text(), nullable=False),
        sa.Column("total_ads_analyzed", sa.Integer(), nullable=False),
        sa.Column("insights_fingerprint", sa.Text(), nullable=False),
        # The full AI-generated payload stored as JSON
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
    )
    op.create_index("idx_brand_recommendations_brand_id", "brand_recommendations", ["brand_id"])


def downgrade() -> None:
    op.drop_index("idx_brand_recommendations_brand_id", table_name="brand_recommendations")
    op.drop_table("brand_recommendations")