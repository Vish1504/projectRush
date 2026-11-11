# /Users/vish1504/projectRush/app/api/decision.py

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.decision import (
    DecisionRequest,
    DecisionResponse,
)
from app.services.decision_service import make_decision


router = APIRouter(
    prefix="/decisions",
    tags=["decisions"],
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

    # Get the shared campaign index for this worker.
    index = http_request.app.state.inverted_index

    # Get this worker's local campaign lease manager.
    campaign_lease_manager = (
        http_request.app.state.campaign_lease_manager
    )

    # The decision service now builds the complete ad pod response.
    return make_decision(
        request,
        db,
        index,
        campaign_lease_manager,
    )