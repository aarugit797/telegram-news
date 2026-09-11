import uuid
from datetime import date, datetime

from sqlalchemy import String, Text, Float, Integer, Boolean, Date, DateTime, ForeignKey, ARRAY, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from pgvector.sqlalchemy import Vector

from app.core.time import utcnow


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
        UUID(as_uuid=True),
        primary_key=True,
        # Both on purpose. default= lets SQLAlchemy know the id before the
        # INSERT, so ORM writes never depend on reading a value back.
        # server_default= covers every write that does not go through the
        # ORM - psql, a migration backfill, another service - where the
        # Python default simply does not exist.
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    source: Mapped[str] = mapped_column(String(50))          # "github" | "hackernews" | "arxiv" | "blogs" | "rss"
    title: Mapped[str] = mapped_column(String(500))
    url: Mapped[str] = mapped_column(String(1000))
    full_content: Mapped[str] = mapped_column(Text)           # full README / abstract / post text
    summary: Mapped[str] = mapped_column(Text)                 # one-line plain English summary

    novelty_score: Mapped[float] = mapped_column(Float)
    relevance_score: Mapped[float] = mapped_column(Float)
    applicability_score: Mapped[float] = mapped_column(Float)
    composite_score: Mapped[float] = mapped_column(Float)
    filter_justification: Mapped[str] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        server_default=text("now()"),
    )

    is_sent: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("batches.id"), nullable=True
    )

    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))

    # 1024 matches Voyage AI's voyage-3 embedding output dimension.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024), nullable=True)


class Batch(Base):
    """
    One row = one actual notification event sent to users. Created by
    the batching agent's batch_assembly step.
    """
    __tablename__ = "batches"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        # Both on purpose. default= lets SQLAlchemy know the id before the
        # INSERT, so ORM writes never depend on reading a value back.
        # server_default= covers every write that does not go through the
        # ORM - psql, a migration backfill, another service - where the
        # Python default simply does not exist.
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=text("now()"))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    signal_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)))
    delivery_status: Mapped[str] = mapped_column(String(20), default="pending", server_default=text("'pending'"))  # pending | delivered | failed


class DailyStat(Base):
    """
    One row = one source's performance summary for one calendar day.
    Written to by a small aggregation step (built later) - purely for
    observability, nothing else in the system reads from this table
    to make decisions.
    """
    __tablename__ = "stats"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        # Both on purpose. default= lets SQLAlchemy know the id before the
        # INSERT, so ORM writes never depend on reading a value back.
        # server_default= covers every write that does not go through the
        # ORM - psql, a migration backfill, another service - where the
        # Python default simply does not exist.
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    # A calendar day, not an instant - so Date, not a timestamp. Storing
    # a day as timestamptz would make "which day is this?" depend on the
    # reader's session timezone.
    date: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(50))

    signals_fetched: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    signals_passed_rules: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    signals_passed_llm: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    signals_sent: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))

    total_tokens_used: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    estimated_cost: Mapped[float] = mapped_column(Float, default=0.0, server_default=text("0"))
