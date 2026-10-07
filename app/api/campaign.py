# /Users/vish1504/projectRush/app/api/campaign.py
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.campaign import CampaignCreate, CampaignResponse
from app.services.campaign_service import create, get_by_id, get_all, activate


router = APIRouter()


@router.post(
    "/campaigns",
    response_model=CampaignResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_campaign( campaign_input: CampaignCreate, db: Session = Depends(get_db)):
    return create(campaign_input, db)


@router.get( "/campaigns/{campaign_id}", response_model=CampaignResponse )
def get_campaign(campaign_id: int, db: Session = Depends(get_db)):
    campaign = get_by_id(campaign_id, db)

    if campaign is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found"
        )

    return campaign

@router.get("/campaigns", response_model=list[CampaignResponse])
def list_all(db: Session = Depends(get_db)):
    return get_all(db)

@router.post(
    "/campaigns/{campaign_id}/activate",
    response_model=CampaignResponse,
)
def activate_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
):
    campaign = activate(campaign_id, db)

    if campaign is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found",
        )

    return campaign