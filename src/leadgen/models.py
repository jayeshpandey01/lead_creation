import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum as SAEnum, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class LeadStatus(str, enum.Enum):
    discovered = "discovered"
    researched = "researched"
    ready_to_send = "ready_to_send"
    sent = "sent"
    replied = "replied"
    bounced = "bounced"
    unsubscribed = "unsubscribed"


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)

    first_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    qualification_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    research_brief: Mapped[str | None] = mapped_column(Text, nullable=True)
    email_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email_body: Mapped[str | None] = mapped_column(Text, nullable=True)

    message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    status: Mapped[LeadStatus] = mapped_column(SAEnum(LeadStatus), default=LeadStatus.discovered, index=True)

    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
