# /Users/vish1504/projectRush/app/models/campaign.py
# Rush Campaign ORM model

# This file represents PostgreSQL

from datetime import datetime

from sqlalchemy import DateTime, Integer, String,CheckConstraint,ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


# This python class describes how a PostgreSQL table called campaigns should look:

# campaigns
# id | name | capacity | status | start_time | end_time
class Campaign(Base):
    # SQLAlchemy maps this Python class to PostgreSQL table: campaigns
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
        CheckConstraint(
            "frequency_cap_per_hour > 0",
            name="ck_campaign_frequency_cap_positive",
            )
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
    frequency_cap_per_hour: Mapped[int]= mapped_column(
        Integer,
        nullable=False,
    )
    

# This is one of the normalized targeting tables
class CampaignRegion(Base):
    __tablename__ = "campaign_regions"

    campaign_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        primary_key=True,
    )

    region: Mapped[str] = mapped_column(
        String(255),
        primary_key=True,
    )
    
# This is one of the normalized targeting tables
class CampaignDevice(Base):
    __tablename__ = "campaign_devices"

    campaign_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        primary_key=True,
    )

    device: Mapped[str] = mapped_column(
        String(255),
        primary_key=True,
    )

    
# This is one of the normalized targeting tables
class CampaignSubscriptionTier(Base):
    __tablename__ = "campaign_subscription_tiers"

    campaign_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        primary_key=True,
    )

    subscription_tier: Mapped[str] = mapped_column(
        String(255),
        primary_key=True,
    )