import uuid
from datetime import datetime

from sqlalchemy import String, Text, Float, Integer, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """
    Separate Base from models_news.py's Base - these two model files
    belong to two DIFFERENT physical databases (News DB vs Conversation
    DB), so they must never share one Base or Alembic will try to
    manage both sets of tables as if they were one database.
    """
    pass


class User(Base):
    """
    One row = one person allowed to interact with the system via
    WhatsApp. Created manually (or via an invite flow, later) - not
    self-serve signup for now, since we're testing with a known
    circle of friends only.
    """
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    whatsapp_number: Mapped[str] = mapped_column(String(20), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)

    registered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_whitelisted: Mapped[bool] = mapped_column(Boolean, default=False)

    daily_message_count: Mapped[int] = mapped_column(Integer, default=0)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Message(Base):
    """
    One row = one single message - either inbound (from the user) or
    outbound (the agent's reply). This is the full conversation log,
    and is what token_budget.py reads the "last N messages" from.
    """
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))

    direction: Mapped[str] = mapped_column(String(10))          
    message_text: Mapped[str] = mapped_column(Text)

    intent_classification: Mapped[str | None] = mapped_column(String(30), nullable=True)

    guardrail_result: Mapped[str | None] = mapped_column(String(30), nullable=True)

    tool_used: Mapped[str | None] = mapped_column(String(30), nullable=True)

    langsmith_run_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tokens_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost: Mapped[float | None] = mapped_column(Float, nullable=True)

    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class Summary(Base):
    """
    One row = one compressed checkpoint of older conversation history,
    created by token_budget.py when a conversation gets too long to
    fit raw messages within the model's context window.
    """
    __tablename__ = "summaries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    summary_text: Mapped[str] = mapped_column(Text)

    covers_from: Mapped[datetime] = mapped_column(DateTime)   
    covers_to: Mapped[datetime] = mapped_column(DateTime)     

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DailyCost(Base):
    """
    One row = one user's running LLM cost total for one calendar day.
    Read and updated by cost_tracker.py after every LLM call in the
    responder flow, to enforce the daily spend cap.
    """
    __tablename__ = "daily_costs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    date: Mapped[datetime] = mapped_column(DateTime)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))

    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)

