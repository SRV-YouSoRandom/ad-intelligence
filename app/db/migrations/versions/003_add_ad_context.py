"""Add ad_context to insights table.

Revision ID: 003
Revises: 002
Create Date: 2026-03-15
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "insights",
        sa.Column(
            "ad_context",
            sa.Text(),
            nullable=False,
            server_default="commercial",
        ),
    )


def downgrade() -> None:
    op.drop_column("insights", "ad_context")