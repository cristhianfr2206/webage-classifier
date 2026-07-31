"""Link browser recovery runs to their static source run."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006c_pilot_stabilization"
down_revision: str | None = "0006b_pilot_memberships"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "browser_inspections",
        sa.Column("source_run_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_browser_inspections_source_run",
        "browser_inspections",
        "classification_runs",
        ["source_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_browser_inspections_source_run_id",
        "browser_inspections",
        ["source_run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_browser_inspections_source_run_id", table_name="browser_inspections")
    op.drop_constraint(
        "fk_browser_inspections_source_run", "browser_inspections", type_="foreignkey"
    )
    op.drop_column("browser_inspections", "source_run_id")
