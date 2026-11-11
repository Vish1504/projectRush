# /Users/vish1504/projectRush/app/models/campaign.py
# Rush Campaign ORM model

# This defines how Rush stores campaigns in PostgreSQL

from datetime import datetime

from sqlalchemy import DateTime, Integer, String,CheckConstraint,ForeignKey, func
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
            ),
        CheckConstraint(
                    "remaining_unleased_capacity >= 0",
                    name="ck_remaining_unleased_capacity_not_negative",
                    ),
        CheckConstraint(
                    "remaining_unleased_capacity <= capacity",
                    name="ck_remaining_unleased_capacity_not_above_capacity",
                ),
        CheckConstraint(
                    "duration_seconds > 0",
                    name="ck_campaign_duration_positive",
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
    
    remaining_unleased_capacity: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
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
    duration_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    
    competitive_group: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    

# This is this the region targeting table for campaign regions
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

class CampaignLease(Base):
    __tablename__ = "campaign_leases"
    
    __table_args__ = (
            CheckConstraint(
                "requested_units > 0",
                name="ck_requested_units_positive",
            ),
            CheckConstraint(
                "granted_units <= requested_units",
                name="ck_granted_vs_request_units",
            ),
            CheckConstraint(
                "granted_units > 0",
                name="ck_granted_units_positive",
                ),
    )
            

    lease_request_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )
    
    campaign_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        nullable=False
    )
    
    worker_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )
    
    requested_units: Mapped[int] = mapped_column(
        Integer,
        nullable=False
    )
    
    granted_units: Mapped[int] = mapped_column(
        Integer,
        nullable=False
    )
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    
    
    
    
    
    
    
    
        
    
    
# This is this the device targeting table for campaign devices
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

    
# This is this the subscription_tiers targeting table for campaign subscription
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