"""Add content_signature_json to pages (M3 Tier B: near-duplicate at scale)

Stores a compact per-page MinHash signature so fuzzy near-duplicate clustering
can run from signatures (no full page text kept). See modules/near_duplicate.py.

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-09-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, Sequence[str], None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("pages", sa.Column("content_signature_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("pages", "content_signature_json")
