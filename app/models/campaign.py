# /Users/vish1504/projectRush/app/models/campaign.py
# Rush Campaign ORM model

from datetime import datetime

from sqlalchemy import DateTime, Integer, String,CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


# This python class describes how a PostgreSQL table called campaigns should look
class Campaign(Base):
    __tablename__ = "campaigns"
    
    __table_args__ = (
        CheckConstraint(
            "capacity > 0",
            name="ck_campaign_capacity_positive",
        ),
        CheckConstraint(
            "end_time > start_time",
            name="ck_campaign_time_window",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    capacity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    end_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="DRAFT",
    )
    