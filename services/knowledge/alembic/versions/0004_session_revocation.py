"""session revocation: users.tokens_valid_after (Stage 13)

JWTs issued before this timestamp are rejected in get_current_user. Stamped on
ban so a banned account can't resume on its old cookie after being un-banned.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0004_session_revocation"
down_revision: Union[str, None] = "0003_plans_and_usage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("tokens_valid_after", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "tokens_valid_after")
