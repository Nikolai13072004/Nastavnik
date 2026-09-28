"""Corporate pilot: member groups and address-bound course invitations."""
from alembic import op
import sqlalchemy as sa

revision = "0013_course_pilot"
down_revision = "0012_account_deletion_jobs"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "workspace_members",
        sa.Column("group_name", sa.String(120), nullable=False, server_default=""),
    )
    op.create_table(
        "course_invitations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("group_name", sa.String(120), nullable=False, server_default=""),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("invited_by_user_id", sa.String(36), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_course_invitations_workspace_id", "course_invitations", ["workspace_id"])
    op.create_index("ix_course_invitations_email", "course_invitations", ["email"])
    op.create_index("ix_course_invitations_invited_by_user_id", "course_invitations", ["invited_by_user_id"])
    op.create_index("ix_course_invitations_expires_at", "course_invitations", ["expires_at"])


def downgrade():
    op.drop_table("course_invitations")
    op.drop_column("workspace_members", "group_name")
