# /Users/vish1504/projectRush/app/api/campaign.py
from fastapi import APIRouter, HTTPException, status

from app.schemas.campaign import CampaignCreate, CampaignResponse
from app.services.campaign_service import create, get_by_id,get_all

router = APIRouter()


@router.post(
    "/campaigns",
    response_model=CampaignResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_campaign(campaign_input: CampaignCreate):
    return create(campaign_input)


@router.get(
    "/campaigns/{campaign_id}",
    response_model=CampaignResponse,
)
def get_campaign(campaign_id: int):
    campaign = get_by_id(campaign_id)

    if campaign is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found",
        )

    return campaign

@router.get("/campaigns",response_model=list[CampaignResponse])
def list_campaigns():
    return get_all()