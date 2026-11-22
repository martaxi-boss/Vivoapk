"""Initial independent Vivoapk mapping and authentication tables."""
import sqlalchemy as sa
from alembic import op

revision = "20261122_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "subscription_mappings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("mega_subscription_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("dns_link", sa.Text(), nullable=False),
        sa.Column("dns_link_samsung_lg", sa.Text(), nullable=True),
        sa.Column("expiring_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("mega_subscription_id", name="uq_vivoapk_mega_id"),
        sa.UniqueConstraint("username", name="uq_vivoapk_username"),
    )
    op.create_table(
        "auth_rate_windows",
        sa.Column("bucket_key", sa.String(length=80), primary_key=True),
        sa.Column("window_number", sa.BigInteger(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("auth_rate_windows")
    op.drop_table("subscription_mappings")
