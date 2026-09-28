"""course creation capability: users.can_create_courses (Stage 30)

Per-user flag gating course creation. Default off - a superuser grants it to
teachers via the admin panel. See ``src/courses.py::can_create_courses``.

The ``server_default`` is kept (not dropped afterwards) so the migration runs
cleanly on SQLite without a batch table-rebuild; the ORM sets the value
explicitly on insert, so the DB-level default only matters for the backfill of
existing rows.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0010_user_can_create_courses"
down_revision: Union[str, None] = "0009_email_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "can_create_courses",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "can_create_courses")
