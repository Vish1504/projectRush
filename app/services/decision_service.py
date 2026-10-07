# /Users/vish1504/projectRush/app/services/decision_service.py
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.repositories.campaign_repository import CampaignRepository
from app.schemas.decision import DecisionRequest
from app.models.campaign import Campaign
from app.runtime.allocation_store import AllocationStore, AllocationResult
from app.runtime.redis_client import redis_client
from redis.exceptions import RedisError
    
# Here we discover eligible campaigns & then try to safely allocate one.
def make_decision(
    decision_request: DecisionRequest,
    db: Session,
) -> Campaign| None:
    try:
        decision_time = datetime.now(timezone.utc)
        
        # We will fetch the campaign data from here and discover eligible campaigns 
        repository = CampaignRepository(db)
        
        # allocation_store(Redis) will later add filters for capacity, frequency caps, and 
        # duplicate-request protection.
        allocation_store = AllocationStore(redis_client)
        
        # We can first check if this exact request_id has already produced a decision earlier.
        existing_campaign_id = allocation_store.get_existing_decision(
                                decision_request.request_id
                                )
        # return the earlier winning campaign instead of allocating again.
        if existing_campaign_id is not None:
            return repository.get_by_id(existing_campaign_id)
        
        # Now we check PostgreSQL for campaigns that are currently eligible based on lifecycle state, time window 
        # and targeting.
        candidates = repository.find_candidates(
            region=decision_request.region,
            device=decision_request.device,
            subscription_tier=decision_request.subscription_tier,
            decision_time=decision_time,
        )
        
        for candidate_campaign in candidates:
            # Redis performs the final atomic safety check:
            result= allocation_store.try_allocate(candidate_campaign.id,decision_request.request_id,decision_request.viewer_id,candidate_campaign.frequency_cap_per_hour,decision_time)
            
            # Another Rush worker may have completed this same request after our initial duplicate check but before this allocation attempt.
            if result == AllocationResult.ALREADY_PROCESSED:
                # To identify which campaign won the original request.
                existing_campaign_id = allocation_store.get_existing_decision(decision_request.request_id)
                # Fetch that earlier winner campaign from PostgreSQL and return the same decision.
                existing_campaign = repository.get_by_id(existing_campaign_id)
                return existing_campaign

            # If all checks pass, Redis consumes one unit of capacity and increments the viewer's frequency counter.
            if result==AllocationResult.ALLOCATED:
                return candidate_campaign

        # If every candidate failed, Rush could not allocate an ad.
        return None
    
    # If Redis throws connection/timeout/etc error
    except RedisError:
        return None
    
    
# only to discover campaigns that are eligible in PostgreSQL.
# def find_candidates(
#     decision_request: DecisionRequest,
#     db: Session,
# ) -> list[Campaign]:
#     decision_time = datetime.now(timezone.utc)
#     repository = CampaignRepository(db)
    
#     return repository.find_candidates(
#         region=decision_request.region,
#         device=decision_request.device,
#         subscription_tier=decision_request.subscription_tier,
#         decision_time=decision_time,
#     )
            
            
            
            
            
        
