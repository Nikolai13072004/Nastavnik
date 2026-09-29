"""assignments & attempts: teacher-assigned tests + student results (Stage 18)

Two additive tables. ``assignments`` stores a course's saved quiz (questions as a
JSON snapshot, draft/published flag); ``assignment_attempts`` stores one row per
(assignment, student) with the server-computed score — re-submit overwrites via
the unique constraint. Both cascade off their parents. See
``design/assignments-and-analytics.md``.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0006_assignments"
down_revision: Union[str, None] = "0005_courses_and_roles"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "assignments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("source_label", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("questions", sa.JSON(), nullable=False),
        sa.Column("is_published", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_assignments_workspace_id"), "assignments", ["workspace_id"], unique=False)
    op.create_index(
        op.f("ix_assignments_created_by_user_id"), "assignments", ["created_by_user_id"], unique=False
    )

    op.create_table(
        "assignment_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("assignment_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("answers", sa.JSON(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assignment_id"], ["assignments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("assignment_id", "user_id", name="uq_attempt_per_user"),
    )
    op.create_index(
        op.f("ix_assignment_attempts_assignment_id"),
        "assignment_attempts",
        ["assignment_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_assignment_attempts_user_id"), "assignment_attempts", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_assignment_attempts_user_id"), table_name="assignment_attempts")
    op.drop_index(op.f("ix_assignment_attempts_assignment_id"), table_name="assignment_attempts")
    op.drop_table("assignment_attempts")
    op.drop_index(op.f("ix_assignments_created_by_user_id"), table_name="assignments")
    op.drop_index(op.f("ix_assignments_workspace_id"), table_name="assignments")
    op.drop_table("assignments")
