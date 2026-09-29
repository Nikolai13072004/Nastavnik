"""courses & roles: workspace kind/join_code/join_enabled + users.active_workspace_id (Stage 15)

A course is a shared workspace. ``kind`` separates a personal space from a
course, ``join_code`` / ``join_enabled`` drive join-by-code, and
``users.active_workspace_id`` is the soft pointer to the user's selected
workspace (no FK by design — it's re-validated against membership on resolve,
so a deleted course just falls back to the personal workspace).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0005_courses_and_roles"
down_revision: Union[str, None] = "0004_session_revocation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="personal"),
    )
    op.add_column(
        "workspaces",
        sa.Column("join_code", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "workspaces",
        sa.Column("join_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "users",
        sa.Column("active_workspace_id", sa.String(length=36), nullable=True),
    )
    op.create_index(op.f("ix_workspaces_join_code"), "workspaces", ["join_code"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_workspaces_join_code"), table_name="workspaces")
    op.drop_column("users", "active_workspace_id")
    op.drop_column("workspaces", "join_enabled")
    op.drop_column("workspaces", "join_code")
    op.drop_column("workspaces", "kind")
