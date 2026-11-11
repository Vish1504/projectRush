# /Users/vish1504/projectRush/app/services/campaign_service.py

# This file is used for when the application wants to create a campaign
from sqlalchemy.orm import Session

from app.models.campaign import Campaign,CampaignLease
from app.repositories.campaign_repository import CampaignRepository
from app.schemas.campaign import CampaignCreate, CampaignStatus
from app.runtime.allocation_store import AllocationStore
from app.runtime.redis_client import redis_client

# creates campaign in PostgreSQL (but not in redis yet)
def create(
    campaign_input: CampaignCreate,
    db: Session,
) -> Campaign:

    # Create the repository and give it this request's Session.
    repository = CampaignRepository(db)

    # Convert API/domain input into the SQLAlchemy ORM model that will be stored in PostgreSQL.
    # We have now created a Python SQLAlchemy object
    campaign = Campaign(
        name=campaign_input.name,
        capacity=campaign_input.capacity,
        start_time=campaign_input.start_time,
        end_time=campaign_input.end_time,
        status=CampaignStatus.DRAFT.value,
        frequency_cap_per_hour=campaign_input.frequency_cap_per_hour,
        duration_seconds=campaign_input.duration_seconds,
        competitive_group=campaign_input.competitive_group,
    )
    
    
    # Repository handles persistence.
    try:
        repository.add(campaign)

        repository.add_targeting(
            campaign,
            campaign_input.regions,
            campaign_input.devices,
            campaign_input.subscription_tiers,
        )

        db.commit()
        db.refresh(campaign)

        return campaign

    except Exception:
        db.rollback()
        raise


def get_by_id(
    campaign_id: int,
    db: Session,
) -> Campaign | None:

    repository = CampaignRepository(db)

    return repository.get_by_id(campaign_id)


def get_all(
    db: Session,
) -> list[Campaign]:

    repository = CampaignRepository(db)

    return repository.get_all()


def activate(campaign_id: int, db: Session) -> Campaign | None:
    repository = CampaignRepository(db)

    campaign = repository.get_by_id(campaign_id)

    if campaign is None:
        return None
    
    if campaign.status == CampaignStatus.ACTIVE.value:
        return campaign
    
    
    
    
    campaign.status = CampaignStatus.ACTIVE.value
    
    # Initialize leasing capacity only on first activation.
    # By default prior to the first activation, remaining_unleased_capacity=NULL
    if campaign.remaining_unleased_capacity is None:
        campaign.remaining_unleased_capacity=campaign.capacity

    db.commit()
    
    # Notify workers that campaign configuration has changed
    redis_client.incr("rush:index:version")
    
    db.refresh(campaign)

    return campaign