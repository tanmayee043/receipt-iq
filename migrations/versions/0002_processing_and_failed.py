"""Allow the processing and failed statuses.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_documents_status", "documents", type_="check")
    op.create_check_constraint(
        "ck_documents_status",
        "documents",
        "status IN ('processing', 'passed', 'needs_review', 'dead_letter', 'failed', "
        "'corrected', 'dismissed')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM documents WHERE status IN ('processing', 'failed')")
    op.drop_constraint("ck_documents_status", "documents", type_="check")
    op.create_check_constraint(
        "ck_documents_status",
        "documents",
        "status IN ('passed', 'needs_review', 'dead_letter', 'corrected', 'dismissed')",
    )
