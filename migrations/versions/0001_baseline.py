"""Baseline: schema as of June 2026 (pre-Alembic).

This revision is intentionally a no-op. The schema it represents is the
one produced by app/database.py:init_db() — Base.metadata.create_all plus
the idempotent PRAGMA-guarded column additions and indexes that predate
Alembic in this project.

- Existing databases: mark them as being at this revision once with
      alembic stamp 0001
- Fresh databases: init_db() still creates the full schema at startup;
  stamp them the same way before applying future migrations.

All schema changes from here on should be real Alembic revisions
(alembic revision --autogenerate -m "...") instead of new entries in the
init_db() column lists.

Revision ID: 0001
Revises:
Create Date: 2026-06-11
"""

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
