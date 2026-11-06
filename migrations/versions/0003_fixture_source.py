"""Record where each fixture row came from (provider or round file)

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-06 10:30:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("fixtures") as batch:
        batch.add_column(sa.Column("source", sa.String(length=64), nullable=False,
                                   server_default="football_data_couk"))
    # Fixtures that exist only because a round file referenced them: no provider result,
    # odds or stats.
    op.execute("""
        UPDATE fixtures SET source = 'round_file'
        WHERE id IN (SELECT fixture_id FROM super6_round_fixtures)
          AND id NOT IN (SELECT fixture_id FROM fixture_results)
          AND id NOT IN (SELECT fixture_id FROM market_snapshots)
          AND id NOT IN (SELECT fixture_id FROM fixture_stats)
    """)


def downgrade() -> None:
    with op.batch_alter_table("fixtures") as batch:
        batch.drop_column("source")
