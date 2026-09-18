"""add password_reset_requests table

Revision ID: a1b2c3d4e5f6
Revises: f3a3b5ecebce
Create Date: 2026-09-18 10:00:00.000000

Admin-resolved password reset (no email service): a user files a request,
the workspace admin resolves it by issuing a temporary password. See
worker/auth.py (create_password_reset_request / resolve_password_reset_request)
and api/auth.py's admin-* actions.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'f3a3b5ecebce'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'password_reset_requests',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('status', sa.String(length=20), server_default='pending', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('resolved_by', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['resolved_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_password_reset_requests_email'),
        'password_reset_requests', ['email'], unique=False,
    )
    op.create_index(
        op.f('ix_password_reset_requests_status'),
        'password_reset_requests', ['status'], unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_password_reset_requests_status'), table_name='password_reset_requests')
    op.drop_index(op.f('ix_password_reset_requests_email'), table_name='password_reset_requests')
    op.drop_table('password_reset_requests')
