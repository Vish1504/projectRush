# /Users/vish1504/projectRush/app/api/decision.py 
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.decision import DecisionRequest,DecisionResponse, DecisionStatusEnum
from app.schemas.campaign import CampaignResponse
from app.services.decision_service import find_candidates,make_decision


router = APIRouter(
    prefix="/decisions",
    tags=["decisions"]
)

@router.post(
    "",
    response_model=DecisionResponse,
)
def win_decision(
    request: DecisionRequest,
    db: Session = Depends(get_db),
):
    # Ask the decision service to choose and allocate one campaign
    winner_candidate= make_decision(request, db)
    
    if winner_candidate is None:
        return DecisionResponse(
            request_id=request.request_id,
            status=DecisionStatusEnum.NO_FILL,
            campaign_id=None
        )
    
    return DecisionResponse(
        request_id=request.request_id,
        status=DecisionStatusEnum.ALLOCATED,
        campaign_id=winner_candidate.id
    )

@router.post(
    "/candidates",
    response_model=list[CampaignResponse],
)

def get_candidates(
    request: DecisionRequest,
    db: Session = Depends(get_db),
):
    # By doing this, we're getting ALL the eligible candidates
    return find_candidates(request, db)