"""course lifecycle: workspaces.is_archived (Stage 22)

Additive flag. An archived course is frozen/read-only (``can()`` allows only
``view``), hidden from the active switcher, and not joinable. See
``design/courses-and-roles.md``.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0008_course_archive"
down_revision: Union[str, None] = "0007_chat_history"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("workspaces", "is_archived")
