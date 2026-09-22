"""Add pages.depth and crawls.theme_scores_json (M3 Tier C #7 + Tier B #5)

Two additions computed in finalize_crawl once a crawl completes:
  - pages.depth: shortest click-depth from the homepage (BFS over the internal
    link graph; 0 = homepage, NULL = orphan/unreachable). Lets the Pages grid
    sort by depth server-side (M3 Tier C #7).
  - crawls.theme_scores_json: a per-theme 0-100 score for the crawl, derived
    from its Issue rows, so the Compare tab can show a per-theme score trend
    across crawls (M3 Tier B #5).

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-09-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, Sequence[str], None] = "e6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("pages", sa.Column("depth", sa.Integer(), nullable=True))
    op.add_column("crawls", sa.Column("theme_scores_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("crawls", "theme_scores_json")
    op.drop_column("pages", "depth")
