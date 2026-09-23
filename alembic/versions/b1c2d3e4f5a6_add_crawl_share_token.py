"""Add crawls.share_token (M5 T5.2 shareable report links)

An opaque, unguessable token that turns a crawl into a public, read-only
shared report. NULL = not shared. Set/cleared via api/crawls.py
(setShare/revokeShare); the public read path (api/share.py) resolves a crawl
by this token, bypassing org-scoping - so the token itself is the capability
and must be long + random. Unique so a token maps to exactly one crawl.

Revision ID: b1c2d3e4f5a6
Revises: a8b9c0d1e2f3
Create Date: 2026-09-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, Sequence[str], None] = "a8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("crawls", sa.Column("share_token", sa.String(length=64), nullable=True))
    op.create_index("ix_crawls_share_token", "crawls", ["share_token"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_crawls_share_token", table_name="crawls")
    op.drop_column("crawls", "share_token")
