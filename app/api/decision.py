# /Users/vish1504/projectRush/app/api/decision.py 
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.decision import DecisionRequest
from app.schemas.campaign import CampaignResponse
from app.services.decision_service import find_candidates


router = APIRouter(
    prefix="/decisions",
    tags=["decisions"],
)


@router.post(
    "/candidates",
    response_model=list[CampaignResponse],
)
def get_candidates(
    request: DecisionRequest,
    db: Session = Depends(get_db),
):
    return find_candidates(request, db)