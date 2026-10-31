# /Users/vish1504/projectRush/app/services/decision_service.py
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.repositories.campaign_repository import CampaignRepository
from app.schemas.decision import DecisionRequest
from app.models.campaign import Campaign
from app.runtime.allocation_store import AllocationStore, AllocationResult
from app.runtime.redis_client import redis_client
from redis.exceptions import RedisError
import logging

from app.services.inverted_index import InvertedIndex

logger = logging.getLogger(__name__)
    
def ensure_index_current(index, repository):
    # 1. Read the current version from Redis
    redis_version=redis_client.get("rush:index:version")
    
    # A missing redis version means we cannot establish freshness
    if redis_version is None:
        return False
    
    # Convert Redis's stored value into an integer
    redis_version = int(redis_version)
    
    # 2. Compare against the local index version
    local_version=index._state["version"]
    
    if redis_version == local_version:
        return True
    
    # if the redis and local versions differ, rebuild the index from PostgreSQL
    repository.load_index(index, version=redis_version)
    # Redis may have changed again while we were rebuilding
    latest_redis_version = redis_client.get("rush:index:version")
    if latest_redis_version is None:
        return False
        
    # Proceed only if the rebuilt index matches Redis
    return index._state["version"] == int(latest_redis_version)

# Here we discover eligible campaigns & then try to safely allocate one.
def make_decision(
    decision_request: DecisionRequest,
    db: Session,
    index: InvertedIndex
) -> Campaign | None:
    try:
        decision_time = datetime.now(timezone.utc)

        # PostgreSQL provides durable campaign eligibility data
        repository = CampaignRepository(db)

        # Redis handles capacity, frequency caps, and idempotency
        allocation_store = AllocationStore(redis_client)

        # Check whether this request was already allocated previously
        existing_campaign_id = allocation_store.get_existing_decision(
            decision_request.request_id
        )

        # Return the previous decision without allocating again
        if existing_campaign_id is not None:
            return repository.get_by_id(existing_campaign_id)

        # Maximum two attempts: initial attempt + one retry
        for attempt in range(2):

            # Ensure the local index is current before candidate discovery
            index_is_ready = ensure_index_current(index, repository)

            if not index_is_ready:
                return None  # Fail closed if the index cannot be refreshed

            # Discover targeting matches and capture the SAME snapshot version
            candidate_ids, snapshot_version = index.find_candidates(
                decision_request.region,
                decision_request.device,
                decision_request.subscription_tier,
            )

            # PostgreSQL validates campaign status and time window
            candidates = repository.get_candidates_by_ids(
                candidate_ids,
                decision_time,
            )

            # Track whether Redis rejected an outdated index
            stale_index = False

            for candidate_campaign in candidates:

                # Redis performs atomic version, capacity, frequency,
                # and duplicate-request checks
                result = allocation_store.try_allocate(
                    candidate_campaign.id,
                    decision_request.request_id,
                    decision_request.viewer_id,
                    candidate_campaign.frequency_cap_per_hour,
                    decision_time,
                    snapshot_version,
                )

                # Another worker may have allocated this same request
                if result == AllocationResult.ALREADY_PROCESSED:
                    existing_campaign_id = allocation_store.get_existing_decision(
                        decision_request.request_id
                    )

                    return repository.get_by_id(existing_campaign_id)

                # The configuration changed after candidate discovery
                if result == AllocationResult.STALE_INDEX:
                    stale_index = True
                    break  # Abandon remaining candidates from this snapshot

                # Successful allocation
                if result == AllocationResult.ALLOCATED:
                    return candidate_campaign

                # Other failures (capacity/frequency) allow trying
                # the next eligible campaign

            # No version mismatch occurred.
            # All eligible candidates were attempted without allocation.
            if not stale_index:
                return None

            # If stale_index is True, the outer loop repeats:
            # 1. Refresh index
            # 2. Rediscover candidates
            # 3. Attempt allocation again

        # Both attempts exhausted: fail closed
        return None

    except RedisError:
        logger.exception("Redis failure during decision allocation")
        return None

            
        
