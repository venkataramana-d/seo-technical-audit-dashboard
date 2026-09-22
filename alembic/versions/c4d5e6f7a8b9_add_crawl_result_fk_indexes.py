"""Add indexes on crawl-result foreign keys (M2 scale)

Every listing/aggregation query in api/crawls.py filters or joins on these hot
foreign keys (pages.crawl_id, issues.crawl_id, issues.page_id, links.page_id).
Without indexes each paginated request is a full table scan; at 2,000+ pages
that is the dominant cost. These indexes make the paginated crawl views fast.

Revision ID: c4d5e6f7a8b9
Revises: b2c3d4e5f6a7
Create Date: 2026-09-22
"""
from typing import Sequence, Union

from alembic import op


revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(op.f("ix_pages_crawl_id"), "pages", ["crawl_id"], unique=False)
    op.create_index(op.f("ix_issues_crawl_id"), "issues", ["crawl_id"], unique=False)
    op.create_index(op.f("ix_issues_page_id"), "issues", ["page_id"], unique=False)
    op.create_index(op.f("ix_links_page_id"), "links", ["page_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_links_page_id"), table_name="links")
    op.drop_index(op.f("ix_issues_page_id"), table_name="issues")
    op.drop_index(op.f("ix_issues_crawl_id"), table_name="issues")
    op.drop_index(op.f("ix_pages_crawl_id"), table_name="pages")
