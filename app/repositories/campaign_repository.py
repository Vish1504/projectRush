# /Users/vish1504/projectRush/app/repositories/campaign_repository.py
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.models.campaign import Campaign


class CampaignRepository:
    def __init__(self, db: Session):
        # Store the Session so repository methods
        # can use it to communicate with PostgreSQL.
        self.db = db

    def add(self, campaign: Campaign) -> Campaign:
        # Tell SQLAlchemy to persist this Campaign object.
        self.db.add(campaign)

        # Commit the transaction so the campaign
        # is permanently stored in PostgreSQL.
        self.db.commit()

        # Reload values from PostgreSQL.
        # Important for things like the generated id.
        self.db.refresh(campaign)

        return campaign

    def get_by_id(self, campaign_id: int) -> Campaign | None:
        # Campaign.id is the primary key, so Session.get()
        # can retrieve it directly.
        return self.db.get(Campaign, campaign_id)

    def get_all(self) -> list[Campaign]:
        # SELECT * FROM campaigns
        statement = select(Campaign)

        return list(
            self.db.scalars(statement).all()
        )