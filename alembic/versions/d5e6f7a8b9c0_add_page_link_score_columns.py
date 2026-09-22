"""Add link_score / inlinks / outlinks to pages (M3 Tier B)

Persists the internal-PageRank Link Score and internal in/out link counts per
page (computed over the whole link graph in finalize_crawl), so the Pages grid
can sort and filter on them server-side instead of recomputing the graph on
every request.

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-09-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, Sequence[str], None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("pages", sa.Column("link_score", sa.Integer(), nullable=True))
    op.add_column("pages", sa.Column("inlinks", sa.Integer(), nullable=True))
    op.add_column("pages", sa.Column("outlinks", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("pages", "outlinks")
    op.drop_column("pages", "inlinks")
    op.drop_column("pages", "link_score")
