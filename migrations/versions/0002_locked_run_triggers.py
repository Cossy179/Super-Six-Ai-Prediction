"""Database triggers that make locked prediction runs immutable

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-05 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from super6.storage import triggers

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    triggers.install(op.get_bind())


def downgrade() -> None:
    triggers.uninstall(op.get_bind())
