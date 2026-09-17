import time
import uuid
from pathlib import Path

from sqlalchemy import JSON, Float, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


def identifier():
    return uuid.uuid4().hex


class Document(Base):
    __tablename__ = 'documents'
    __table_args__ = (UniqueConstraint('organization_id', 'sha256'),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=identifier)
    organization_id: Mapped[str] = mapped_column(String(160), index=True)
    file_name: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(80))
    file_size: Mapped[int] = mapped_column(Integer)
    storage_path: Mapped[str] = mapped_column(String(512))
    sha256: Mapped[str] = mapped_column(String(64))
    page_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40), default='UPLOADED', index=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Job(Base):
    __tablename__ = 'ocr_jobs'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=identifier)
    document_id: Mapped[str] = mapped_column(ForeignKey('documents.id'), unique=True)
    status: Mapped[str] = mapped_column(String(32), default='queued', index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    pages_processed: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[float] = mapped_column(Float, default=time.time)
    lease_until: Mapped[float] = mapped_column(Float, default=0)
    run_token: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    processing_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_required: Mapped[bool] = mapped_column(default=False)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    history: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    completed_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class OCRResult(Base):
    __tablename__ = 'ocr_results'
    __table_args__ = (UniqueConstraint('document_id', 'page_number'),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=identifier)
    document_id: Mapped[str] = mapped_column(ForeignKey('documents.id'), index=True)
    page_number: Mapped[int] = mapped_column(Integer)
    extracted_text: Mapped[str] = mapped_column(Text)
    ocr_engine: Mapped[str] = mapped_column(String(40))
    ocr_version: Mapped[str] = mapped_column(String(100))
    processing_time: Mapped[float] = mapped_column(Float)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


Path('data').mkdir(exist_ok=True)
engine = create_engine(settings().database_url, pool_pre_ping=True,
                       connect_args={'check_same_thread': False, 'timeout': 30}
                       if settings().database_url.startswith('sqlite') else {})
Session = sessionmaker(engine, expire_on_commit=False)


def init_db():
    Base.metadata.create_all(engine)
