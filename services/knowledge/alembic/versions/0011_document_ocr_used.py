"""document OCR flag: documents.ocr_used (Stage 46)

Marks a Document whose text was extracted via OCR (scans / image-based slides),
so the UI can warn that formulas/diagrams may be imprecise and should be checked
against the original. ``server_default`` is kept (not dropped) so the column
backfills to false on existing rows without a SQLite batch table-rebuild; the
ORM sets the value explicitly, so the DB default only matters for the backfill.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0011_document_ocr_used"
down_revision: Union[str, None] = "0010_user_can_create_courses"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "ocr_used",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("documents", "ocr_used")
