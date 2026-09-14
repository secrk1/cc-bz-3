"""add file_transfers table

Revision ID: 0003_transfers
Revises: 0002_summary
Create Date: 2026-09-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_transfers"
down_revision: Union[str, None] = "0002_summary"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "file_transfers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("transfer_id", sa.String(64), nullable=False, unique=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"),
                  nullable=False, index=True),
        sa.Column("asset_id", sa.Integer(), sa.ForeignKey("assets.id"),
                  nullable=False, index=True),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("remote_path", sa.Text(), nullable=True),
        sa.Column("size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("bytes_done", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("md5", sa.String(32), nullable=True, index=True),
        sa.Column("status", sa.String(16), nullable=False,
                  server_default="uploading", index=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("file_transfers")
