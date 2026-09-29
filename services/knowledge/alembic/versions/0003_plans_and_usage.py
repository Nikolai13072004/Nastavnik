"""plans + usage metering (Stage 12)

Adds ``users.plan`` (personal billing plan: free/pro) and the ``usage_events``
metering ledger. No foreign keys on usage_events by design — the ledger must
outlive the user/workspace it references, for accounting.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0003_plans_and_usage"
down_revision: Union[str, None] = "0002_audit_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("plan", sa.String(length=16), nullable=False, server_default="free"),
    )

    op.create_table(
        "usage_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("billing_subject_type", sa.String(length=16), nullable=False),
        sa.Column("billing_subject_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("usage_events", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_usage_events_workspace_id"), ["workspace_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_usage_events_user_id"), ["user_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_usage_events_action"), ["action"], unique=False)
        batch_op.create_index(batch_op.f("ix_usage_events_billing_subject_id"), ["billing_subject_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_usage_events_created_at"), ["created_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("usage_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_usage_events_created_at"))
        batch_op.drop_index(batch_op.f("ix_usage_events_billing_subject_id"))
        batch_op.drop_index(batch_op.f("ix_usage_events_action"))
        batch_op.drop_index(batch_op.f("ix_usage_events_user_id"))
        batch_op.drop_index(batch_op.f("ix_usage_events_workspace_id"))

    op.drop_table("usage_events")
    op.drop_column("users", "plan")
