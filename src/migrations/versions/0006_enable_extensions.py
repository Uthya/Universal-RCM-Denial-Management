"""enable_extensions: pgvector (required for RAG scaffolding below).

pg_partman and pg_cron are NOT created here — they require superuser and the
shared remote cluster does not have them by default. Partition management is
done manually in alembic; MV refreshes can be scheduled via the host crontab
or pg_cron when available.

Revision ID: 0006_enable_extensions
Revises: 0005_add_ncci_lcd
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006_enable_extensions"
down_revision: str | Sequence[str] | None = "0005_add_ncci_lcd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Required for the rag scaffolding migration that follows
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # Optional — only created if already installed at OS level
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'pg_partman') THEN
                CREATE EXTENSION IF NOT EXISTS pg_partman;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'pg_cron') THEN
                CREATE EXTENSION IF NOT EXISTS pg_cron;
            END IF;
        END$$;
        """
    )


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS pg_cron")
    op.execute("DROP EXTENSION IF EXISTS pg_partman")
    op.execute("DROP EXTENSION IF EXISTS vector")
