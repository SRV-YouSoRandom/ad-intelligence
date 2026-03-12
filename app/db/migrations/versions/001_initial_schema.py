"""Initial schema — brands, ads, insights, jobs tables.

Revision ID: 001
Revises: None
Create Date: 2024-01-01 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Brands table
    op.create_table(
        "brands",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("page_id", sa.Text(), nullable=False, unique=True),
        sa.Column("page_name", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ad_count", sa.BigInteger(), server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    # Ads table
    op.create_table(
        "ads",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("ad_archive_id", sa.Text(), nullable=False, unique=True),
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id", ondelete="CASCADE"), nullable=True),
        sa.Column("page_name", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("ad_type", sa.Text(), nullable=True),
        sa.Column("classification_method", sa.Text(), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("link_title", sa.Text(), nullable=True),
        sa.Column("link_description", sa.Text(), nullable=True),
        sa.Column("cta_type", sa.Text(), nullable=True),
        sa.Column("publisher_platforms", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("impressions_lower", sa.BigInteger(), nullable=True),
        sa.Column("impressions_upper", sa.BigInteger(), nullable=True),
        sa.Column(
            "impressions_mid",
            sa.BigInteger(),
            sa.Computed(
                "CASE WHEN impressions_lower IS NOT NULL AND impressions_upper IS NOT NULL "
                "THEN (impressions_lower + impressions_upper) / 2 ELSE NULL END",
                persisted=True,
            ),
        ),
        sa.Column("reach_lower", sa.BigInteger(), nullable=True),
        sa.Column("reach_upper", sa.BigInteger(), nullable=True),
        sa.Column(
            "reach_mid",
            sa.BigInteger(),
            sa.Computed(
                "CASE WHEN reach_lower IS NOT NULL AND reach_upper IS NOT NULL "
                "THEN (reach_lower + reach_upper) / 2 ELSE NULL END",
                persisted=True,
            ),
        ),
        sa.Column("spend_lower", sa.Numeric(), nullable=True),
        sa.Column("spend_upper", sa.Numeric(), nullable=True),
        sa.Column("snapshot_url", sa.Text(), nullable=True),
        sa.Column("media_local_path", sa.Text(), nullable=True),
        sa.Column("frame_paths", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("frame_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("performance_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("performance_label", sa.Text(), nullable=True),
        sa.Column("performance_percentile", sa.Numeric(5, 2), nullable=True),
        sa.Column("raw_meta_json", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.CheckConstraint("ad_type IN ('STATIC', 'VIDEO', 'UNKNOWN')", name="ck_ads_ad_type"),
        sa.CheckConstraint("performance_label IN ('STRONG', 'AVERAGE', 'WEAK')", name="ck_ads_performance_label"),
    )
    op.create_index("idx_ads_brand_id", "ads", ["brand_id"])
    op.create_index("idx_ads_is_active", "ads", ["is_active"])
    op.create_index("idx_ads_ad_type", "ads", ["ad_type"])
    op.create_index("idx_ads_performance_label", "ads", ["performance_label"])

    # Insights table
    op.create_table(
        "insights",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("ad_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ads.id", ondelete="CASCADE"), unique=True, nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("factors", postgresql.JSONB(), nullable=False),
        sa.Column("model_used", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False, server_default=sa.text("'v1'")),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    # Jobs table
    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("job_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )


def downgrade() -> None:
    op.drop_table("insights")
    op.drop_table("jobs")
    op.drop_index("idx_ads_performance_label", table_name="ads")
    op.drop_index("idx_ads_ad_type", table_name="ads")
    op.drop_index("idx_ads_is_active", table_name="ads")
    op.drop_index("idx_ads_brand_id", table_name="ads")
    op.drop_table("ads")
    op.drop_table("brands")
