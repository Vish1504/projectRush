# /Users/vish1504/projectRush/app/services/campaign_services.py
from app.schemas.campaign import CampaignCreate, CampaignResponse, CampaignStatus

campaigns: dict[int, CampaignResponse] = {}


def create(campaign_input: CampaignCreate) -> CampaignResponse:
    campaign_id = len(campaigns) + 1

    # Build the complete campaign Rush will store and return.
    # The client provides campaign_input; Rush owns id and status.
    campaign_output = CampaignResponse(
        id=campaign_id,
        name=campaign_input.name,
        capacity=campaign_input.capacity,
        start_time=campaign_input.start_time,
        end_time=campaign_input.end_time,
        status=CampaignStatus.DRAFT,
    )

    campaigns[campaign_id] = campaign_output

    return campaign_output


def get_by_id(campaign_id: int) -> CampaignResponse | None:
    return campaigns.get(campaign_id)

def get_all() -> list[CampaignResponse] | None:
    return list(campaigns.values())
