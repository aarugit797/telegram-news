import uuid
from datetime import datetime

from sqlalchemy import String, Text, Float, Integer, Boolean, DateTime, ForeignKey, ARRAY
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from pgvector.sqlalchemy import Vector


class Base(DeclarativeBase):
    """
    Every table class below inherits from this. SQLAlchemy uses this
    shared base to know which Python classes represent database tables
    at all, and to collect them together when generating migrations.
    """
    pass


class Signal(Base):
    """
    One row = one piece of news that passed BOTH stages of the hybrid
    filter (rules engine + LLM judge). Nothing that failed the filter
    is ever written here.
    """
    __tablename__ = "signals"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    source: Mapped[str] = mapped_column(String(50))          
    title: Mapped[str] = mapped_column(String(500))
    url: Mapped[str] = mapped_column(String(1000))
    full_content: Mapped[str] = mapped_column(Text)            
    summary: Mapped[str] = mapped_column(Text)                 

    novelty_score: Mapped[float] = mapped_column(Float)
    relevance_score: Mapped[float] = mapped_column(Float)
    applicability_score: Mapped[float] = mapped_column(Float)
    composite_score: Mapped[float] = mapped_column(Float)
    filter_justification: Mapped[str] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    is_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("batches.id"), nullable=True
    )

    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)

    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024), nullable=True)


class Batch(Base):
    """
    One row = one actual notification event sent to users. Created by
    the batching agent's batch_assembly step.
    """
    __tablename__ = "batches"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    user_count: Mapped[int] = mapped_column(Integer, default=0)
    signal_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)))
    delivery_status: Mapped[str] = mapped_column(String(20), default="pending")


class DailyStat(Base):
    """
    One row = one source's performance summary for one calendar day.
    Written to by a small aggregation step (built later) - purely for
    observability, nothing else in the system reads from this table
    to make decisions.
    """
    __tablename__ = "stats"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    date: Mapped[datetime] = mapped_column(DateTime)
    source: Mapped[str] = mapped_column(String(50))

    signals_fetched: Mapped[int] = mapped_column(Integer, default=0)
    signals_passed_rules: Mapped[int] = mapped_column(Integer, default=0)
    signals_passed_llm: Mapped[int] = mapped_column(Integer, default=0)
    signals_sent: Mapped[int] = mapped_column(Integer, default=0)

    total_tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0.0)