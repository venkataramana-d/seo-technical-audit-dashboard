"""Auth hardening: users.password_changed_at + auth_attempts table

Session revocation (reject sessions issued before a password change) and
DB-backed rate limiting for login / password-reset.

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-09-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, Sequence[str], None] = "f7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_changed_at", sa.DateTime(), nullable=True))
    op.create_table(
        "auth_attempts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ident", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index(op.f("ix_auth_attempts_ident"), "auth_attempts", ["ident"], unique=False)
    op.create_index(op.f("ix_auth_attempts_created_at"), "auth_attempts", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_auth_attempts_created_at"), table_name="auth_attempts")
    op.drop_index(op.f("ix_auth_attempts_ident"), table_name="auth_attempts")
    op.drop_table("auth_attempts")
    op.drop_column("users", "password_changed_at")
