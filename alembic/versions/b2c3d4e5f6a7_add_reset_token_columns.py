"""add token columns to password_reset_requests

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-18 11:00:00.000000

Self-serve email password reset: the emailed link carries a random token; only
its SHA-256 hash + a short expiry are stored. NULL for admin-resolved requests.
See worker/email_send.py and api/auth.py (request-password-reset /
reset-password-with-token).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('password_reset_requests', sa.Column('token_hash', sa.String(length=64), nullable=True))
    op.add_column('password_reset_requests', sa.Column('token_expires_at', sa.DateTime(), nullable=True))
    op.create_index(
        op.f('ix_password_reset_requests_token_hash'),
        'password_reset_requests', ['token_hash'], unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_password_reset_requests_token_hash'), table_name='password_reset_requests')
    op.drop_column('password_reset_requests', 'token_expires_at')
    op.drop_column('password_reset_requests', 'token_hash')
