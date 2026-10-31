# /Users/vish1504/projectRush/app/api/decision.py 
from fastapi import APIRouter, Depends , Request
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.decision import DecisionRequest,DecisionResponse, DecisionStatusEnum
from app.schemas.campaign import CampaignResponse
from app.services.decision_service import make_decision


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
    http_request: Request,
    db: Session = Depends(get_db),
):
    # Retrieve the shared index created during FastAPI startup
    index = http_request.app.state.inverted_index
    
    # Ask the decision service to choose and allocate the winner campaign
    winner_candidate= make_decision(request, db, index)
    
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

