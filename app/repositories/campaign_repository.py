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

from app.services.inverted_index import InvertedIndex
index = InvertedIndex()


# ============================================================
# CAMPAIGN REPOSITORY — FUNCTION FLOW
# ============================================================
#
# 1. CREATING A CAMPAIGN (Python -> PostgreSQL)
#
#    Advertiser submits campaign + targeting details
#                       |
#                       v
#              add(campaign)
#                       |
#                       v
#              campaigns table
#              (main campaign row)
#                       |
#                       v
#              add_targeting(...)
#                       |
#           +-----------+-----------+
#           |           |           |
#           v           v           v
#       regions      devices    subscription_tiers
#        table        table          table
#           |           |           |
#           +-----------+-----------+
#                       |
#                       v
#              Transaction COMMIT
#              (done by caller)
#
#
# 2. READING CAMPAIGNS (PostgreSQL -> Python)
#
#                   CampaignRepository
#                          |
#           +--------------+---------------+
#           |              |               |
#           v              v               v
#       get_all()    get_by_id(id)  get_active_campaigns()
#           |              |               |
#           v              v               v
#      All campaigns  One campaign    ACTIVE campaigns
#                          |               |
#                          v               v
#                  get_campaign_targeting(id)
#                          |
#                          v
#               Read targeting tables
#                          |
#                          v
#                  Return dictionary
#                 (regions, devices,
#                  subscription_tiers)
#
#
# 3. BUILDING THE IN-MEMORY INDEX 
#
#             get_active_campaigns()
#                       |
#                       v
#             For each ACTIVE campaign
#                       |
#                       v
#            get_campaign_targeting(id)
#                       |
#                       v
#               Targeting dictionary
#                       |
#                       v
#              InvertedIndex.build_index()
#                       |
#                       v
#               In-memory snapshot
#               (targeting registers)
#
#    Note: The index-loading connection what I'm working on now
#          It is not yet implemented in this repository.
#
#
# 4. EXISTING SQL CANDIDATE DISCOVERY 
#
#          Viewer sends decision request
#                       |
#                       v
#              find_candidates(...)
#                       |
#                       v
#               Query PostgreSQL:
#               - Campaign is ACTIVE
#               - Within time window
#               - Region matches / unrestricted
#               - Device matches / unrestricted
#               - Tier matches / unrestricted
#                       |
#                       v
#             Return matching Campaigns
#
# ============================================================



class CampaignRepository:


    def __init__(self, db: Session):
        # To store the database session for our queries
        self.db = db
        
    # To create the main campaign record. It will add on to the main campaigns table
    def add(self, campaign: Campaign) -> Campaign:
         # Add a new campaign to the database session
        self.db.add(campaign)
        # Send the INSERT to PostgreSQL and generate the campaign ID
        # But this does not commit the transaction
        self.db.flush()
        return campaign

    # It takes targeting information supplied for a campaign and prepares rows to be written into PostgreSQL.
    # python -> PostgreSQL
    def add_targeting(
        self,
        campaign: Campaign,
        regions: list[str],
        devices: list[str],
        subscription_tiers: list[str],
    ) -> None:
        # Store each targeted region as a separate database row in the table CampaignRegion
        for region in regions:
            self.db.add(
                CampaignRegion(
                    campaign_id=campaign.id,
                    region=region,
                )
            )
        # Store each targeted device as a separate database row in the table CampaignDevice
        for device in devices:
            self.db.add(
                CampaignDevice(
                    campaign_id=campaign.id,
                    device=device,
                )
            )
        # Store each targeted subscription tier as a separate row in the table CampaignSubscriptionTier
        for tier in subscription_tiers:
            self.db.add(
                CampaignSubscriptionTier(
                    campaign_id=campaign.id,
                    subscription_tier=tier,
                )
            )
        # Empty lists create no rows, meaning unrestricted targeting

    # Find one campaign by its campaign_id (primary key)
    def get_by_id(self, campaign_id: int) -> Campaign | None:
        # Return None if the campaign doesn't exist
        return self.db.get(Campaign, campaign_id)
    
    # Fetch only ACTIVE campaigns
    # PostgreSQL → Python
    def get_active_campaigns(self) -> list[Campaign]:
            return list(
                self.db.scalars(
                    select(Campaign).where(Campaign.status == "ACTIVE")
                ).all()
            )
    
     # Fetch all campaigns
    def get_all(self) -> list[Campaign]:
        return list(
            self.db.scalars(
                select(Campaign)
            ).all()
        )
    
    # Retrieve one campaign's targeting   
    # PostgreSQL → Python dictionary
    def get_campaign_targeting(self, campaign_id):
         # Check that the campaign actually exists
        if self.get_by_id(campaign_id) == None:
            return None
    
        # Get all regions for this campaign
        region_statement=select(CampaignRegion.region).where(
                    CampaignRegion.campaign_id == campaign_id
                )
        
        regions_list = list(self.db.scalars(region_statement).all())
    
        # Get all devices for this campaign
        device_statement=select(CampaignDevice.device).where(
                    CampaignDevice.campaign_id == campaign_id
                )
        
        devices_list = list(self.db.scalars(device_statement).all())
    
        # Get all subscription tiers for this campaign
        subscription_statement=select(CampaignSubscriptionTier.subscription_tier).where(
            CampaignSubscriptionTier.campaign_id == campaign_id
        )
        subscription_tiers_list = list(self.db.scalars(subscription_statement).all())
        
        # Return the structure expected by InvertedIndex
        return {
            "id": campaign_id,
            "regions": regions_list,
            "devices": devices_list,
            "subscription_tiers": subscription_tiers_list
        }

    # This is the main function to find eligible candidates 
    # This is the SQL-based candidate discovery
    def find_candidates(
        self,
        region: str,
        device: str,
        subscription_tier: str,
        decision_time: datetime,
    ) -> list[Campaign]:
        
        # Check whether a campaign explicitly targets this region
        region_matches = exists().where(
            CampaignRegion.campaign_id == Campaign.id,
            CampaignRegion.region == region,
        )
        
        # No region rows means the campaign targets ALL regions
        region_unrestricted = ~exists().where(
            CampaignRegion.campaign_id == Campaign.id,
        )
        
         # Check whether the campaign targets this device
        device_matches = exists().where(
            CampaignDevice.campaign_id == Campaign.id,
            CampaignDevice.device == device,
        )
        
         # No device rows means ALL devices are allowed
        device_unrestricted = ~exists().where(
            CampaignDevice.campaign_id == Campaign.id,
        )
        
        # Check whether the campaign targets this subscription tier
        tier_matches = exists().where(
            CampaignSubscriptionTier.campaign_id == Campaign.id,
            CampaignSubscriptionTier.subscription_tier == subscription_tier,
        )
        
        # No subscription rows means ALL tiers are allowed
        tier_unrestricted = ~exists().where(
            CampaignSubscriptionTier.campaign_id == Campaign.id,
        )
        
        # A campaign must be ACTIVE and within the start and end time
        statement = select(Campaign).where(
            Campaign.status == "ACTIVE",
            Campaign.start_time <= decision_time,
            Campaign.end_time > decision_time,
            or_(region_matches, region_unrestricted),
            or_(device_matches, device_unrestricted),
            or_(tier_matches, tier_unrestricted),
        )
        
         # Execute the query and return matching Campaign objects
        return list(self.db.scalars(statement).all())
    
    def load_index(self, index, version):
        active_campaigns = self.get_active_campaigns()

        campaign_data_list = []

        for campaign in active_campaigns:
            # Get this campaign's targeting dictionary and add it to campaign_data_list
            campaign_dict=self.get_campaign_targeting(campaign.id)
            if campaign_dict is not None:
                campaign_data_list.append(campaign_dict)
        index.build_index(campaign_data_list, version=version)
        
    
    # To take the IDs, retrieve their corresponding Campaign objects from PostgreSQL, and filter out campaigns that are no longer ACTIVE as per the campgin time duration.
    def get_candidates_by_ids(
        self,
        candidate_ids: set[int],
        decision_time: datetime
    ) -> list[Campaign]:
         # No candidate IDs means no database query is necessary
        if not candidate_ids:
            return []
        
        # SQL query describing which rows to retrieve.
        sql_query = select(Campaign).where(Campaign.id.in_(candidate_ids),
                                           Campaign.status == "ACTIVE" , 
                                           Campaign.start_time<=decision_time,
                                           Campaign.end_time>decision_time )                  
                    
        
        # Executes the query and retrieves the matching Campaign objects using our DB session
        result = self.db.scalars(sql_query).all()
        # Return the Campaign objects as a Python list
        return list(result)
    
   
    
