"""Record how each odds snapshot's availability time is known

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-06 15:30:00

observed: we downloaded the prices at ``available_at`` (upcoming fixtures).
inferred: ``available_at`` comes from the provider's collection schedule (historical files).
kickoff:  closing prices, available at kick-off.
unknown:  pre-match prices whose quote time can't be established.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("market_snapshots") as batch:
        batch.add_column(sa.Column("availability_basis", sa.String(length=16), nullable=False,
                                   server_default="inferred"))
    op.execute("UPDATE market_snapshots SET availability_basis = 'kickoff' "
               "WHERE odds_type = 'closing'")
    # Pre-match prices from a file downloaded before kick-off were stamped with the download
    # time itself, so they match their source snapshot's retrieval time exactly.
    op.execute("""
        UPDATE market_snapshots SET availability_basis = 'observed'
        WHERE odds_type = 'prematch' AND EXISTS (
            SELECT 1 FROM source_snapshots s
            WHERE s.id = market_snapshots.source_snapshot_id
              AND s.retrieved_at = market_snapshots.available_at)
    """)


    # Pre-match prices stamped at or after kick-off (an older version stamped prices from a
    # file downloaded after kick-off with the kick-off time): when they were quoted is
    # unknown. They were never usable before kick-off anyway.
    op.execute("""
        UPDATE market_snapshots SET availability_basis = 'unknown'
        WHERE odds_type = 'prematch' AND availability_basis = 'inferred' AND EXISTS (
            SELECT 1 FROM fixtures f
            WHERE f.id = market_snapshots.fixture_id
              AND market_snapshots.available_at >= f.kickoff_utc)
    """)


def downgrade() -> None:
    with op.batch_alter_table("market_snapshots") as batch:
        batch.drop_column("availability_basis")
