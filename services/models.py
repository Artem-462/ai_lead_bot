from datetime import UTC, datetime
from typing import ClassVar

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String, nullable=True)
    language: Mapped[str] = mapped_column(String, default="uk", server_default="uk")
    first_seen: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, server_default=func.current_timestamp()
    )
    last_active: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, server_default=func.current_timestamp(), onupdate=utc_now
    )


class History(Base):
    __tablename__ = "history"
    __table_args__: ClassVar[dict[str, bool]] = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    role: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, server_default=func.current_timestamp(), index=True
    )


class Document(Base):
    __tablename__ = "documents"
    __table_args__: ClassVar[dict[str, bool]] = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(collation="NOCASE"), unique=True)
    file_type: Mapped[str] = mapped_column(String)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    embedding_model: Mapped[str] = mapped_column(
        String, default="gemini-embedding-001", server_default="gemini-embedding-001"
    )
    embedding_dim: Mapped[int] = mapped_column(Integer, default=3072, server_default=text("3072"))
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, server_default=func.current_timestamp()
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__: ClassVar[dict[str, bool]] = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    page_number: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[bytes] = mapped_column(LargeBinary)


class Lead(Base):
    __tablename__ = "leads"
    __table_args__: ClassVar[tuple[CheckConstraint, dict[str, bool]]] = (
        CheckConstraint("status IN ('pending', 'sent', 'failed')", name="ck_leads_status"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_uuid: Mapped[str] = mapped_column(String, unique=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    username: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String)
    phone: Mapped[str] = mapped_column(String)
    request_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String, default="pending", server_default="pending", index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, server_default=func.current_timestamp()
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class FSMState(Base):
    __tablename__ = "fsm_states"

    storage_key: Mapped[str] = mapped_column(String, primary_key=True)
    state: Mapped[str | None] = mapped_column(String, nullable=True)
    data_json: Mapped[str] = mapped_column(Text, default="{}", server_default=text("'{}'"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, server_default=func.current_timestamp(), onupdate=utc_now
    )
