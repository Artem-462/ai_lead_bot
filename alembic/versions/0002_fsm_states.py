import sqlalchemy as sa

from alembic import context, op

revision = "0002_fsm_states"
down_revision = "0001_code_first"
branch_labels = None
depends_on = None


def upgrade() -> None:
    offline = context.is_offline_mode()
    tables = set() if offline else set(sa.inspect(op.get_bind()).get_table_names())
    if "fsm_states" not in tables:
        op.create_table(
            "fsm_states",
            sa.Column("storage_key", sa.String(), primary_key=True, nullable=False),
            sa.Column("state", sa.String(), nullable=True),
            sa.Column("data_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        )


def downgrade() -> None:
    op.drop_table("fsm_states")
