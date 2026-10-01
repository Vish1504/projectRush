# /Users/vish1504/projectRush/app/services/campaign_service.py
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

    # Convert API/domain input into the SQLAlchemy ORM model
    # that will be stored in PostgreSQL.
    campaign = Campaign(
        name=campaign_input.name,
        capacity=campaign_input.capacity,
        start_time=campaign_input.start_time,
        end_time=campaign_input.end_time,
        status=CampaignStatus.DRAFT.value,
    )

    # Repository handles persistence.
    return repository.add(campaign)


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