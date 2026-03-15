"""Add political ad fields and estimated_audience_size to ads table.

New columns:
  - disclaimer: "Paid for by" text — presence = political/issue ad
  - bylines: alternative political disclaimer format
  - beneficiary_payers: EU political transparency (who paid, who benefits)
  - demographic_distribution: age/gender breakdown (political + EU)
  - delivery_by_region: region-level reach (political + EU)
  - languages: detected ad languages
  - currency: currency of spend figures
  - estimated_audience_lower/upper: audience size range

Revision ID: 004
Revises: 003
Create Date: 2026-03-15
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Political ad identification fields
    op.add_column("ads", sa.Column("disclaimer", sa.Text(), nullable=True))
    op.add_column("ads", sa.Column("bylines", sa.Text(), nullable=True))
    op.add_column("ads", sa.Column("beneficiary_payers", postgresql.JSONB(), nullable=True))

    # Demographic & geographic breakdown (political + EU)
    op.add_column("ads", sa.Column("demographic_distribution", postgresql.JSONB(), nullable=True))
    op.add_column("ads", sa.Column("delivery_by_region", postgresql.JSONB(), nullable=True))

    # Metadata
    op.add_column("ads", sa.Column("languages", postgresql.ARRAY(sa.Text()), nullable=True))
    op.add_column("ads", sa.Column("currency", sa.Text(), nullable=True))

    # Estimated audience size (separate from impressions)
    op.add_column("ads", sa.Column("estimated_audience_lower", sa.BigInteger(), nullable=True))
    op.add_column("ads", sa.Column("estimated_audience_upper", sa.BigInteger(), nullable=True))

    # Index on disclaimer for fast political ad filtering
    op.create_index("idx_ads_disclaimer", "ads", ["disclaimer"],
                    postgresql_where=sa.text("disclaimer IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("idx_ads_disclaimer", table_name="ads")
    op.drop_column("ads", "estimated_audience_upper")
    op.drop_column("ads", "estimated_audience_lower")
    op.drop_column("ads", "currency")
    op.drop_column("ads", "languages")
    op.drop_column("ads", "delivery_by_region")
    op.drop_column("ads", "demographic_distribution")
    op.drop_column("ads", "beneficiary_payers")
    op.drop_column("ads", "bylines")
    op.drop_column("ads", "disclaimer")