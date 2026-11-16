import sqlalchemy as sa

from alembic import context, op

revision = "0001_code_first"
down_revision = None
branch_labels = None
depends_on = None


def _create_users() -> None:
    op.create_table(
        "users",
        sa.Column("user_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("username", sa.String(), nullable=True),
        sa.Column("language", sa.String(), nullable=False, server_default="uk"),
        sa.Column("first_seen", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("last_active", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
    )


def _create_history() -> None:
    op.create_table(
        "history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sqlite_autoincrement=True,
    )


def _create_documents() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("filename", sa.String(collation="NOCASE"), nullable=False, unique=True),
        sa.Column("file_type", sa.String(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("embedding_model", sa.String(), nullable=False, server_default="gemini-embedding-001"),
        sa.Column("embedding_dim", sa.Integer(), nullable=False, server_default=sa.text("3072")),
        sa.Column("uploaded_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sqlite_autoincrement=True,
    )


def _create_document_chunks() -> None:
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("doc_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", sa.LargeBinary(), nullable=False),
        sqlite_autoincrement=True,
    )


def _create_leads() -> None:
    op.create_table(
        "leads",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("lead_uuid", sa.String(), nullable=False, unique=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("phone", sa.String(), nullable=False),
        sa.Column("request_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'sent', 'failed')", name="ck_leads_status"),
        sqlite_autoincrement=True,
    )


def _sequence(table: str) -> int | None:
    connection = op.get_bind()
    if not sa.inspect(connection).has_table("sqlite_sequence"):
        return None
    return connection.scalar(sa.text("SELECT seq FROM sqlite_sequence WHERE name = :table"), {"table": table})


def _restore_sequence(table: str, sequence: int | None) -> None:
    if sequence is None:
        return
    connection = op.get_bind()
    values = {"table": table, "sequence": sequence}
    result = connection.execute(
        sa.text("UPDATE sqlite_sequence SET seq = MAX(seq, :sequence) WHERE name = :table"), values
    )
    if not result.rowcount:
        connection.execute(sa.text("INSERT INTO sqlite_sequence (name, seq) VALUES (:table, :sequence)"), values)


def _migrate_users() -> None:
    connection = op.get_bind()
    columns = {column["name"] for column in sa.inspect(connection).get_columns("users")}
    if "username" not in columns:
        op.add_column("users", sa.Column("username", sa.String(), nullable=True))
    for name in ("first_seen", "last_active"):
        if name not in columns:
            op.add_column("users", sa.Column(name, sa.DateTime(), nullable=True))

    first_seen = "created_at" if "created_at" in columns else "CURRENT_TIMESTAMP"
    last_active = "updated_at" if "updated_at" in columns else "CURRENT_TIMESTAMP"
    connection.execute(sa.text(
        f"UPDATE users SET language = COALESCE(language, 'uk'), "
        f"first_seen = COALESCE(first_seen, {first_seen}, CURRENT_TIMESTAMP), "
        f"last_active = COALESCE(last_active, {last_active}, CURRENT_TIMESTAMP)"
    ))
    with op.batch_alter_table("users", recreate="always") as batch:
        batch.alter_column("user_id", type_=sa.BigInteger(), nullable=False)
        batch.alter_column("username", type_=sa.String(), nullable=True)
        batch.alter_column("language", type_=sa.String(), nullable=False, server_default="uk")
        batch.alter_column("first_seen", type_=sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp())
        batch.alter_column("last_active", type_=sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp())
        for name in ("created_at", "updated_at"):
            if name in columns:
                batch.drop_column(name)


def _restore_history_users() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("""
        INSERT OR IGNORE INTO users (user_id, language, first_seen, last_active)
        SELECT user_id, 'uk', COALESCE(MIN(timestamp), CURRENT_TIMESTAMP),
               COALESCE(MAX(timestamp), CURRENT_TIMESTAMP)
        FROM history WHERE user_id IS NOT NULL GROUP BY user_id
    """))
    connection.execute(sa.text("""
        UPDATE users SET username = (
            SELECT h.username FROM history h
            WHERE h.user_id = users.user_id AND h.username IS NOT NULL
            ORDER BY h.id DESC LIMIT 1
        ) WHERE username IS NULL
    """))


def _migrate_history() -> None:
    sequence = _sequence("history")
    op.rename_table("history", "_history_before_code_first")
    _create_history()
    connection = op.get_bind()
    for role, content, offset in (("user", "user_message", 1), ("assistant", "bot_response", 0)):
        connection.execute(sa.text(
            f"INSERT INTO history (id, user_id, role, content, timestamp) "
            f"SELECT id * 2 - {offset}, user_id, :role, COALESCE({content}, ''), "
            f"COALESCE(timestamp, CURRENT_TIMESTAMP) FROM _history_before_code_first"
        ), {"role": role})
    op.drop_table("_history_before_code_first")
    _restore_sequence("history", sequence * 2 if sequence is not None else None)


def _migrate_documents() -> None:
    connection = op.get_bind()
    sequence = _sequence("documents")
    columns = {column["name"] for column in sa.inspect(connection).get_columns("documents")}
    if "embedding_model" not in columns:
        op.add_column("documents", sa.Column(
            "embedding_model", sa.String(), nullable=False, server_default="gemini-embedding-001"
        ))
    if "embedding_dim" not in columns:
        op.add_column("documents", sa.Column(
            "embedding_dim", sa.Integer(), nullable=False, server_default=sa.text("3072")
        ))
    connection.execute(sa.text("""
        UPDATE documents SET chunk_count = COALESCE(chunk_count, 0),
            embedding_model = COALESCE(embedding_model, 'gemini-embedding-001'),
            embedding_dim = COALESCE(embedding_dim, 3072),
            uploaded_at = COALESCE(uploaded_at, CURRENT_TIMESTAMP)
    """))
    with op.batch_alter_table("documents", recreate="always", table_kwargs={"sqlite_autoincrement": True}) as batch:
        batch.alter_column("id", type_=sa.Integer(), nullable=False)
        batch.alter_column("filename", type_=sa.String(collation="NOCASE"), nullable=False)
        batch.alter_column("file_type", type_=sa.String(), nullable=False)
        batch.alter_column("chunk_count", type_=sa.Integer(), nullable=False, server_default=sa.text("0"))
        batch.alter_column("embedding_model", type_=sa.String(), nullable=False, server_default="gemini-embedding-001")
        batch.alter_column("embedding_dim", type_=sa.Integer(), nullable=False, server_default=sa.text("3072"))
        batch.alter_column("uploaded_at", type_=sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp())
    _restore_sequence("documents", sequence)


def _migrate_document_chunks() -> None:
    connection = op.get_bind()
    sequence = _sequence("document_chunks")
    foreign_keys = sa.inspect(connection).get_foreign_keys("document_chunks")
    convention = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}
    with op.batch_alter_table(
        "document_chunks", recreate="always", naming_convention=convention,
        table_kwargs={"sqlite_autoincrement": True},
    ) as batch:
        batch.alter_column("id", type_=sa.Integer(), nullable=False)
        for foreign_key in foreign_keys:
            if foreign_key["constrained_columns"] == ["doc_id"]:
                name = foreign_key["name"] or f"fk_document_chunks_doc_id_{foreign_key['referred_table']}"
                batch.drop_constraint(name, type_="foreignkey")
        batch.create_foreign_key(
            "fk_document_chunks_doc_id_documents", "documents", ["doc_id"], ["id"], ondelete="CASCADE"
        )
    _restore_sequence("document_chunks", sequence)


def upgrade() -> None:
    offline = context.is_offline_mode()
    tables = set() if offline else set(sa.inspect(op.get_bind()).get_table_names())
    if "users" in tables:
        _migrate_users()
    else:
        _create_users()

    if "history" in tables:
        columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("history")}
        if {"user_message", "bot_response"} <= columns:
            _restore_history_users()
            _migrate_history()
    else:
        _create_history()

    if "documents" in tables:
        _migrate_documents()
    else:
        _create_documents()
    if "document_chunks" in tables:
        _migrate_document_chunks()
    else:
        _create_document_chunks()
    if "leads" not in tables:
        _create_leads()

    for table, column in (
        ("history", "user_id"), ("history", "timestamp"),
        ("document_chunks", "doc_id"), ("leads", "user_id"), ("leads", "status"),
    ):
        indexes = set() if offline else {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}
        name = f"ix_{table}_{column}"
        if name not in indexes:
            op.create_index(name, table, [column])


def downgrade() -> None:
    for table in ("leads", "document_chunks", "history", "documents", "users"):
        op.drop_table(table)
