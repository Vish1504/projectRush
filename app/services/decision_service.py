# /Users/vish1504/projectRush/app/services/decision_service.py

from datetime import datetime, timezone

from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.repositories.campaign_repository import CampaignRepository
from app.runtime.allocation_store import AllocationStore, AllocationResult
from app.runtime.campaign_lease_manager import (
    CampaignLeaseManager,
    LeaseReservationResult,
)
from app.runtime.redis_client import redis_client
from app.schemas.decision import DecisionRequest
from app.services.inverted_index import InvertedIndex
import logging


logger = logging.getLogger(__name__)

# This is a safety check to make sure this worker’s in-memory inverted index is not stale before we use it to choose campaigns.
def ensure_index_current(
    index: InvertedIndex,
    repository: CampaignRepository,
) -> bool:

    # Read the version currently published globally in Redis
    redis_version_raw = redis_client.get("rush:index:version")

    if redis_version_raw is None:
        return False

    redis_version = int(redis_version_raw)

    # This worker already has the current version
    if index._state["version"] == redis_version:
        return True

    # This worker is stale, so rebuild its local index from PostgreSQL
    repository.load_index(
        index,
        version=redis_version,
    )

    # Redis may have changed again while we were rebuilding
    latest_version_raw = redis_client.get("rush:index:version")

    if latest_version_raw is None:
        return False

    latest_version = int(latest_version_raw)

    # Only trust the rebuild if it still matches
    # the globally published version
    return index._state["version"] == latest_version

# Here we discover eligible campaigns & then try to safely allocate one.
def make_decision(
    decision_request: DecisionRequest,
    db: Session,
    index: InvertedIndex,
    campaign_lease_manager: CampaignLeaseManager,
) -> Campaign | None:

    try:
        decision_time = datetime.now(timezone.utc)

        # PostgreSQL provides durable campaign eligibility data
        repository = CampaignRepository(db)

        # Redis handles shared frequency, idempotency,
        # and index-version checks
        allocation_store = AllocationStore(redis_client)

        # Check whether this request was already allocated previously
        existing_campaign_id = allocation_store.get_existing_decision(
            decision_request.request_id
        )

        # Return the previous decision without allocating again
        if existing_campaign_id is not None:
            return repository.get_by_id(existing_campaign_id)

        # Maximum two attempts:
        # initial attempt + one retry if the index became stale
        for attempt in range(2):

            # Ensure the local index is current before candidate discovery
            index_is_ready = ensure_index_current(
                index,
                repository,
            )

            if not index_is_ready:
                # Fail closed if the index cannot be refreshed
                return None

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

                # Before Redis can allocate this campaign,
                # this worker must first own one unit of campaign capacity
                reservation_result = (
                    campaign_lease_manager.reserve_one_unit(
                        candidate_campaign.id
                    )
                )

                # This worker cannot acquire any more capacity
                # for this campaign
                if (
                    reservation_result
                    == LeaseReservationResult.NO_UNLEASED_CAPACITY
                ):
                    continue

                # PostgreSQL refill failed even after the bounded retry
                if (
                    reservation_result
                    == LeaseReservationResult.REFILL_FAILED
                ):
                    continue

                # At this point, exactly one campaign unit
                # has been reserved locally for this request

                # Redis now performs the shared atomic checks:
                # index version, frequency cap, and request idempotency
                result = allocation_store.try_allocate(
                    candidate_campaign.id,
                    decision_request.request_id,
                    decision_request.viewer_id,
                    candidate_campaign.frequency_cap_per_hour,
                    decision_time,
                    snapshot_version,
                )

                # Another worker may already have allocated
                # this exact same request
                if result == AllocationResult.ALREADY_PROCESSED:

                    # This attempt did not consume campaign capacity,
                    # so give its locally reserved unit back
                    campaign_lease_manager.refund_one_unit(
                        candidate_campaign.id
                    )

                    existing_campaign_id = (
                        allocation_store.get_existing_decision(
                            decision_request.request_id
                        )
                    )

                    return repository.get_by_id(
                        existing_campaign_id
                    )

                # Campaign configuration changed after candidate discovery
                if result == AllocationResult.STALE_INDEX:

                    # No allocation happened,
                    # so return the reserved campaign unit
                    campaign_lease_manager.refund_one_unit(
                        candidate_campaign.id
                    )

                    stale_index = True

                    # Abandon candidates from this stale snapshot
                    break

                # Successful allocation
                if result == AllocationResult.ALLOCATED:

                    # Do NOT refund.
                    # The reserved campaign unit is now consumed.
                    return candidate_campaign

                # Any other normal Redis rejection,
                # such as frequency capping,
                # means this allocation definitely did not happen.
                # Therefore the local campaign unit is safe to reuse.
                campaign_lease_manager.refund_one_unit(
                    candidate_campaign.id
                )

                # Existing loop naturally tries the next campaign

            # No stale index occurred.
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

        # IMPORTANT:
        # If Redis fails after we reserved local capacity,
        # we do NOT refund that unit.
        #
        # Redis may have completed the Lua operation but lost the response.
        # Burning one unit is safer than accidentally creating capacity.
        logger.exception(
            "Redis failure during decision allocation"
        )

        return None