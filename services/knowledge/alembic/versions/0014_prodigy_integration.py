"""Isolated service credentials for one Prodigy course per organization."""

from alembic import op
import sqlalchemy as sa

revision = "0014_prodigy_integration"
down_revision = "0013_course_pilot"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "prodigy_integrations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(128), nullable=False, unique=True),
        sa.Column("course_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("prodigy_integrations")
