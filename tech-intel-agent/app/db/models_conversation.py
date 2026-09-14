import uuid
from datetime import date, datetime

from sqlalchemy import String, Text, Float, Integer, Boolean, Date, DateTime, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.time import utcnow


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

    # Replaces whatsapp_number. A user is identified by WHICH service and
    # their id ON that service, because those ids are not interchangeable:
    # a Telegram chat_id is a number Telegram assigns, a WhatsApp id is an
    # E.164 phone number.
    channel: Mapped[str] = mapped_column(String(20))            # "telegram" | "whatsapp"
    channel_user_id: Mapped[str] = mapped_column(String(100))   # chat_id, or phone number
    display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)

    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=text("now()"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    is_whitelisted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))

    daily_message_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


    __table_args__ = (
        # Scoped to the channel, NOT globally unique on channel_user_id.
        # The same person legitimately exists as two rows if they use both
        # services - different ids, separate conversation histories,
        # separate daily budgets. A global unique on the id alone would
        # also collide the moment a Telegram chat_id happened to match a
        # phone number, which nothing prevents.
        UniqueConstraint("channel", "channel_user_id", name="uq_users_channel_user"),
    )


class Message(Base):
    """
    One row = one single message - either inbound (from the user) or
    outbound (the agent's reply). This is the full conversation log,
    and is what token_budget.py reads the "last N messages" from.
    """
    __tablename__ = "messages"

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

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))

    direction: Mapped[str] = mapped_column(String(10))          # "inbound" | "outbound"
    message_text: Mapped[str] = mapped_column(Text)

    intent_classification: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # "SMALLTALK" | "NEWS_QUERY" | "NOTIFICATION_FOLLOWUP" | "WEB_QUESTION" | None for outbound

    guardrail_result: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # "PASS" | "INJECTION_DETECTED" | "OFF_TOPIC" | None for outbound

    tool_used: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # "smalltalk" | "news_db" | "notification_history" | "web_search" | None

    langsmith_run_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tokens_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost: Mapped[float | None] = mapped_column(Float, nullable=True)

    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=text("now()"))
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))

    __table_args__ = (
        # get_recent_messages runs on EVERY inbound message - it filters
        # user_id, then sorts by timestamp descending to take the last N
        # for the token budget. Leading with user_id narrows to one
        # conversation first; timestamp second lets Postgres walk the
        # index backwards for the ORDER BY instead of sorting the rows.
        Index("ix_messages_user_timestamp", "user_id", "timestamp"),
    )


class Summary(Base):
    """
    One row = one compressed checkpoint of older conversation history,
    created by token_budget.py when a conversation gets too long to
    fit raw messages within the model's context window.
    """
    __tablename__ = "summaries"

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

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    summary_text: Mapped[str] = mapped_column(Text)

    covers_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))   # earliest message this summary represents
    covers_to: Mapped[datetime] = mapped_column(DateTime(timezone=True))     # latest message this summary represents

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=text("now()"))


class DailyCost(Base):
    """
    One row = one user's running LLM cost total for one calendar day.
    Read and updated by cost_tracker.py after every LLM call in the
    responder flow, to enforce the daily spend cap.
    """
    __tablename__ = "daily_costs"

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

    # A calendar day, not an instant. cost_tracker.py queries this with
    # date.today(), so Date matches exactly instead of relying on
    # Postgres coercing a date to midnight in the session timezone.
    date: Mapped[date] = mapped_column(Date)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))

    llm_calls: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0, server_default=text("0"))

    __table_args__ = (
        # (user_id, date) is this table's real identity - one row per
        # user per day. increment_daily_cost does a check-then-insert,
        # so two concurrent LLM calls for the same user can both miss
        # and both insert, splitting the day's spend across two rows and
        # letting the $0.50 cap be silently exceeded. UNIQUE makes the
        # database refuse the second insert rather than trusting timing.
        #
        # It also protects the read: get_daily_cost uses
        # scalar_one_or_none(), which raises MultipleResultsFound the
        # moment a duplicate pair exists.
        #
        # Doubles as the lookup index for both of those queries - a
        # UNIQUE constraint is backed by a btree on the same columns.
        UniqueConstraint("user_id", "date", name="uq_daily_costs_user_date"),
    )
