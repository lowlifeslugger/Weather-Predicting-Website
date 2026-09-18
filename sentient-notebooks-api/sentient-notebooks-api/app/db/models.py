import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import relationship
from sqlalchemy.types import CHAR, TypeDecorator

from app.db.base import Base


class GUID(TypeDecorator):
    """Portable UUID: real UUID column on Postgres, CHAR(36) on sqlite.

    Lets you develop against sqlite and deploy on Postgres without
    changing the model.
    """

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID())
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        return uuid.UUID(str(value))


class User(Base):
    __tablename__ = "users"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Notebook(Base):
    __tablename__ = "notebooks"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id = Column(GUID(), ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    # Per-notebook now, not a global like the CLI's `/model` command --
    # otherwise one user switching models would switch it for everyone.
    llm_model = Column(String, nullable=False, default="llama-3.1-8b-instant")
    created_at = Column(DateTime, default=datetime.utcnow)

    documents = relationship(
        "Document", back_populates="notebook", cascade="all, delete-orphan"
    )
    notes = relationship(
        "Note", back_populates="notebook", cascade="all, delete-orphan"
    )


class Document(Base):
    __tablename__ = "documents"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    notebook_id = Column(GUID(), ForeignKey("notebooks.id"), nullable=False, index=True)
    filename = Column(String, nullable=False)
    file_hash = Column(String, nullable=False)
    file_size = Column(Integer, nullable=False, default=0)  # bytes, for quota tracking
    storage_key = Column(String, nullable=True)  # where the raw file lives in the storage backend
    chunk_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    notebook = relationship("Notebook", back_populates="documents")


class Note(Base):
    __tablename__ = "notes"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    notebook_id = Column(GUID(), ForeignKey("notebooks.id"), nullable=False, index=True)
    title = Column(String, nullable=False, default="Untitled note")
    content = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    notebook = relationship("Notebook", back_populates="notes")
