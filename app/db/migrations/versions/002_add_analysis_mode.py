"""Add analysis_mode to insights table.

Revision ID: 002
Revises: 001
Create Date: 2026-03-14
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "insights",
        sa.Column(
            "analysis_mode",
            sa.Text(),
            nullable=False,
            server_default="visual",
        ),
    )


def downgrade() -> None:
    op.drop_column("insights", "analysis_mode")