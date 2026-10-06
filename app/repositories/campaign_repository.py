# /Users/vish1504/projectRush/app/repositories/campaign_repository.py
# This is the SQL layer.

from datetime import datetime

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from app.models.campaign import (
    Campaign,
    CampaignRegion,
    CampaignDevice,
    CampaignSubscriptionTier,
)


class CampaignRepository:

    def __init__(self, db: Session):
        self.db = db

    def add(self, campaign: Campaign) -> Campaign:
        self.db.add(campaign)
        self.db.flush()
        return campaign

    def add_targeting(
        self,
        campaign: Campaign,
        regions: list[str],
        devices: list[str],
        subscription_tiers: list[str],
    ) -> None:

        for region in regions:
            self.db.add(
                CampaignRegion(
                    campaign_id=campaign.id,
                    region=region,
                )
            )

        for device in devices:
            self.db.add(
                CampaignDevice(
                    campaign_id=campaign.id,
                    device=device,
                )
            )

        for tier in subscription_tiers:
            self.db.add(
                CampaignSubscriptionTier(
                    campaign_id=campaign.id,
                    subscription_tier=tier,
                )
            )

    def get_by_id(self, campaign_id: int) -> Campaign | None:
        return self.db.get(Campaign, campaign_id)

    def get_all(self) -> list[Campaign]:
        return list(
            self.db.scalars(
                select(Campaign)
            ).all()
        )

    # your existing methods like add(), get_by_id(), get_all(),
    # add_targeting() should stay here

# This is the main function to find eligible candidates
    def find_candidates(
        self,
        region: str,
        device: str,
        subscription_tier: str,
        decision_time: datetime,
    ) -> list[Campaign]:

        region_matches = exists().where(
            CampaignRegion.campaign_id == Campaign.id,
            CampaignRegion.region == region,
        )

        region_unrestricted = ~exists().where(
            CampaignRegion.campaign_id == Campaign.id,
        )

        device_matches = exists().where(
            CampaignDevice.campaign_id == Campaign.id,
            CampaignDevice.device == device,
        )

        device_unrestricted = ~exists().where(
            CampaignDevice.campaign_id == Campaign.id,
        )

        tier_matches = exists().where(
            CampaignSubscriptionTier.campaign_id == Campaign.id,
            CampaignSubscriptionTier.subscription_tier == subscription_tier,
        )

        tier_unrestricted = ~exists().where(
            CampaignSubscriptionTier.campaign_id == Campaign.id,
        )

        statement = select(Campaign).where(
            Campaign.status == "ACTIVE",
            Campaign.start_time <= decision_time,
            Campaign.end_time > decision_time,
            or_(region_matches, region_unrestricted),
            or_(device_matches, device_unrestricted),
            or_(tier_matches, tier_unrestricted),
        )

        return list(self.db.scalars(statement).all())