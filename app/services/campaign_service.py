# /Users/vish1504/projectRush/app/services/campaign_service.py

# This file is used for when the application wants to create a campaign
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.repositories.campaign_repository import CampaignRepository
from app.schemas.campaign import CampaignCreate, CampaignStatus


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
        frequency_cap_per_hour=campaign_input.frequency_cap_per_hour
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