"""add audit_logs.response_summary

Revision ID: 0002_summary
Revises: 0001_initial
Create Date: 2026-09-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_summary"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "audit_logs",
        sa.Column("response_summary", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("audit_logs", "response_summary")
