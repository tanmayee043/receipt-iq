"""Add the documents table.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("digest", sa.String(64), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("provider", sa.String(50)),
        sa.Column("receipt", JSONB),
        sa.Column("issues", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("attempts", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("met_target", sa.Boolean),
        sa.Column("seconds", sa.Numeric(10, 3)),
        sa.Column("failure_kind", sa.String(50)),
        sa.Column("failure_reason", sa.Text),
        sa.Column("corrected_receipt", JSONB),
        sa.Column("confirmed_mismatch", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("corrected_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('passed', 'needs_review', 'dead_letter', 'corrected', 'dismissed')",
            name="ck_documents_status",
        ),
    )
    op.create_index("ix_documents_status_created_at", "documents", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_documents_status_created_at", table_name="documents")
    op.drop_table("documents")
