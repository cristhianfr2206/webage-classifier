"""Persist deterministic non-contiguous pilot membership."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006b_pilot_memberships"
down_revision: str | None = "0006_evaluation_pilots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "websites",
        sa.Column(
            "pilot_eligible",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index("ix_websites_pilot_eligible", "websites", ["pilot_eligible"])
    op.execute(
        """
        UPDATE websites
        SET pilot_eligible = true
        WHERE tranco_rank IS NOT NULL
          AND NOT (
            (domain = 'example.com' AND tranco_rank = 1)
            OR (domain = 'xn--bcher-kva.de' AND tranco_rank = 2)
            OR (domain = 'news.example.co.uk' AND tranco_rank = 3)
          )
        """
    )

    op.add_column("pilot_items", sa.Column("selection_order", sa.Integer(), nullable=True))
    op.add_column("pilot_items", sa.Column("original_tranco_rank", sa.Integer(), nullable=True))
    op.execute(
        """
        WITH ordered AS (
            SELECT pi.id,
                   row_number() OVER (
                       PARTITION BY pi.pilot_id
                       ORDER BY w.tranco_rank ASC, w.id ASC
                   ) AS selection_order,
                   w.tranco_rank
            FROM pilot_items pi
            JOIN websites w ON w.id = pi.website_id
        )
        UPDATE pilot_items pi
        SET selection_order = ordered.selection_order,
            original_tranco_rank = ordered.tranco_rank
        FROM ordered
        WHERE ordered.id = pi.id
        """
    )
    op.alter_column("pilot_items", "selection_order", nullable=False)
    op.alter_column("pilot_items", "original_tranco_rank", nullable=False)
    op.create_unique_constraint(
        "uq_pilot_item_selection_order",
        "pilot_items",
        ["pilot_id", "selection_order"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_pilot_item_selection_order", "pilot_items", type_="unique")
    op.drop_column("pilot_items", "original_tranco_rank")
    op.drop_column("pilot_items", "selection_order")
    op.drop_index("ix_websites_pilot_eligible", table_name="websites")
    op.drop_column("websites", "pilot_eligible")
