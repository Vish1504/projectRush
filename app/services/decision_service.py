# /Users/vish1504/projectRush/app/services/decision_service.py

from datetime import datetime, timezone
import logging

from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.repositories.campaign_repository import CampaignRepository
from app.runtime.allocation_store import (
    AllocationStore,
    PodCommitCandidate,
    PodCommitSlot,
    PodCommitStatus,
    PodSlotCommitStatus,
)
from app.runtime.campaign_lease_manager import (
    CampaignLeaseManager,
    LeaseReservationResult,
)
from app.runtime.redis_client import redis_client
from app.schemas.decision import (
    DecisionRequest,
    DecisionResponse,
    DecisionStatusEnum,
    PodSlot,
    PodSlotSource,
)
from app.services.inverted_index import InvertedIndex
from app.services.pacing import calculate_pacing_score
from app.services.pod_optimizer import build_pod


logger = logging.getLogger(__name__)


# Makes sure this worker is using the latest campaign index.
def ensure_index_current(
    index: InvertedIndex,
    repository: CampaignRepository,
) -> bool:

    redis_version_raw = redis_client.get(
        "rush:index:version"
    )

    if redis_version_raw is None:
        return False

    redis_version = int(redis_version_raw)

    if index._state["version"] == redis_version:
        return True

    repository.load_index(
        index,
        version=redis_version,
    )

    latest_version_raw = redis_client.get(
        "rush:index:version"
    )

    if latest_version_raw is None:
        return False

    latest_version = int(latest_version_raw)

    return (
        index._state["version"]
        == latest_version
    )


def _no_fill_response(
    request: DecisionRequest,
) -> DecisionResponse:

    return DecisionResponse(
        request_id=request.request_id,
        status=DecisionStatusEnum.NO_FILL,
        slots=[],
        filled_duration_seconds=0,
        unfilled_duration_seconds=(
            request.break_duration_seconds
        ),
    )


# Builds the final response from Redis's committed pod result.
def _build_committed_response(
    request: DecisionRequest,
    committed_slots: tuple[PodCommitSlot, ...],
) -> DecisionResponse:

    response_slots = []

    has_commercial = False

    for slot in committed_slots:

        if slot.status == PodSlotCommitStatus.ALLOCATED:
            has_commercial = True

            response_slots.append(
                PodSlot(
                    campaign_id=slot.campaign_id,
                    duration_seconds=slot.duration_seconds,
                    source=PodSlotSource.COMMERCIAL,
                )
            )

        else:
            # Replace a rejected commercial slot with House.
            response_slots.append(
                PodSlot(
                    campaign_id=None,
                    duration_seconds=slot.duration_seconds,
                    source=PodSlotSource.HOUSE,
                )
            )

    filled_duration = sum(
        slot.duration_seconds
        for slot in response_slots
    )

    if not response_slots:
        status = DecisionStatusEnum.NO_FILL

    elif has_commercial:
        status = DecisionStatusEnum.ALLOCATED

    else:
        status = DecisionStatusEnum.DEGRADED_FALLBACK

    return DecisionResponse(
        request_id=request.request_id,
        status=status,
        slots=response_slots,
        filled_duration_seconds=filled_duration,
        unfilled_duration_seconds=max(
            0,
            request.break_duration_seconds
            - filled_duration,
        ),
    )


# Used when Redis may have committed but we lost the result.
def _build_ambiguous_fallback(
    request: DecisionRequest,
    reserved_campaigns: list[Campaign],
) -> DecisionResponse:

    slots = []

    for campaign in reserved_campaigns:
        slots.append(
            PodSlot(
                campaign_id=None,
                duration_seconds=campaign.duration_seconds,
                source=PodSlotSource.HOUSE,
            )
        )

    filled_duration = sum(
        slot.duration_seconds
        for slot in slots
    )

    return DecisionResponse(
        request_id=request.request_id,
        status=DecisionStatusEnum.DEGRADED_FALLBACK,
        slots=slots,
        filled_duration_seconds=filled_duration,
        unfilled_duration_seconds=max(
            0,
            request.break_duration_seconds
            - filled_duration,
        ),
    )


def make_decision(
    decision_request: DecisionRequest,
    db: Session,
    index: InvertedIndex,
    campaign_lease_manager: CampaignLeaseManager,
) -> DecisionResponse:

    decision_time = datetime.now(timezone.utc)

    repository = CampaignRepository(db)
    allocation_store = AllocationStore(redis_client)

    try:
        # Fast path for a request Redis has already committed.
        existing_slots = (
            allocation_store.get_existing_pod_decision(
                decision_request.request_id
            )
        )

        if existing_slots is not None:
            return _build_committed_response(
                decision_request,
                existing_slots,
            )

    except RedisError:
        logger.exception(
            "Redis failure while checking existing decision"
        )

        return _no_fill_response(
            decision_request
        )

    # Initial attempt plus one stale-index retry.
    for attempt in range(2):

        try:
            if not ensure_index_current(
                index,
                repository,
            ):
                return _no_fill_response(
                    decision_request
                )

            candidate_ids, snapshot_version = (
                index.find_candidates(
                    decision_request.region,
                    decision_request.device,
                    decision_request.subscription_tier,
                )
            )

            candidates = (
                repository.get_candidates_by_ids(
                    candidate_ids,
                    decision_time,
                )
            )

            # Campaigns longer than the break cannot be used.
            candidates = [
                campaign
                for campaign in candidates
                if campaign.duration_seconds
                <= decision_request.break_duration_seconds
            ]

            if not candidates:
                return _no_fill_response(
                    decision_request
                )

            campaign_ids = [
                campaign.id
                for campaign in candidates
            ]

            committed_counts = (
                allocation_store
                .get_committed_allocation_counts(
                    campaign_ids
                )
            )

        except RedisError:
            logger.exception(
                "Redis failure before pod construction"
            )

            return _no_fill_response(
                decision_request
            )

        # Pacing only changes preference, never eligibility.
        pacing_scores = {}

        for campaign in candidates:
            pacing_scores[campaign.id] = (
                calculate_pacing_score(
                    campaign=campaign,
                    committed_allocations=(
                        committed_counts[campaign.id]
                    ),
                    now=decision_time,
                )
            )

        proposed_pod = build_pod(
            candidates=candidates,
            break_duration_seconds=(
                decision_request.break_duration_seconds
            ),
            campaign_pacing_scores=pacing_scores,
        )

        if not proposed_pod:
            return _no_fill_response(
                decision_request
            )

        reserved_campaigns = []

        # Only campaigns with local capacity can reach Redis.
        for campaign in proposed_pod:

            reservation_result = (
                campaign_lease_manager.reserve_one_unit(
                    campaign.id
                )
            )

            if (
                reservation_result
                == LeaseReservationResult.RESERVED
            ):
                reserved_campaigns.append(
                    campaign
                )

        # Partial fill is allowed, so failed reservations are just dropped.
        if not reserved_campaigns:
            return _no_fill_response(
                decision_request
            )

        commit_candidates = [
            PodCommitCandidate(
                campaign_id=campaign.id,
                duration_seconds=campaign.duration_seconds,
                frequency_cap_per_hour=(
                    campaign.frequency_cap_per_hour
                ),
            )
            for campaign in reserved_campaigns
        ]

        try:
            commit_result = allocation_store.commit_pod(
                candidates=commit_candidates,
                request_id=decision_request.request_id,
                viewer_id=decision_request.viewer_id,
                decision_time=decision_time,
                index_version=snapshot_version,
            )

        except RedisError:
            # Redis may have committed before the response was lost.
            # Do not refund uncertain campaign capacity.
            logger.exception(
                "Redis failure during pod commit"
            )

            return _build_ambiguous_fallback(
                decision_request,
                reserved_campaigns,
            )

        if (
            commit_result.status
            == PodCommitStatus.STALE_INDEX
        ):
            # Redis definitely committed nothing.
            for campaign in reserved_campaigns:
                campaign_lease_manager.refund_one_unit(
                    campaign.id
                )

            # Try once more using a fresh index.
            continue

        if (
            commit_result.status
            == PodCommitStatus.ALREADY_PROCESSED
        ):
            # These new reservations were not consumed.
            for campaign in reserved_campaigns:
                campaign_lease_manager.refund_one_unit(
                    campaign.id
                )

            return _build_committed_response(
                decision_request,
                commit_result.slots,
            )

        # Refund only campaigns Redis definitely rejected.
        for slot in commit_result.slots:

            if (
                slot.status
                == PodSlotCommitStatus.FREQUENCY_CAPPED
            ):
                campaign_lease_manager.refund_one_unit(
                    slot.campaign_id
                )

        return _build_committed_response(
            decision_request,
            commit_result.slots,
        )

    # Both index attempts failed safely.
    return _no_fill_response(
        decision_request
    )











# from datetime import datetime, timezone

# from redis.exceptions import RedisError
# from sqlalchemy.orm import Session

# from app.models.campaign import Campaign
# from app.repositories.campaign_repository import CampaignRepository
# from app.runtime.allocation_store import AllocationStore, AllocationResult
# from app.runtime.campaign_lease_manager import (
#     CampaignLeaseManager,
#     LeaseReservationResult,
# )
# from app.runtime.redis_client import redis_client
# from app.schemas.decision import DecisionRequest
# from app.services.inverted_index import InvertedIndex
# import logging


# logger = logging.getLogger(__name__)

# # This is a safety check to make sure this worker’s in-memory inverted index is not stale before we use it to choose campaigns.
# def ensure_index_current(
#     index: InvertedIndex,
#     repository: CampaignRepository,
# ) -> bool:

#     # Read the version currently published globally in Redis
#     redis_version_raw = redis_client.get("rush:index:version")

#     if redis_version_raw is None:
#         return False

#     redis_version = int(redis_version_raw)

#     # This worker already has the current version
#     if index._state["version"] == redis_version:
#         return True

#     # This worker is stale, so rebuild its local index from PostgreSQL
#     repository.load_index(
#         index,
#         version=redis_version,
#     )

#     # Redis may have changed again while we were rebuilding
#     latest_version_raw = redis_client.get("rush:index:version")

#     if latest_version_raw is None:
#         return False

#     latest_version = int(latest_version_raw)

#     # Only trust the rebuild if it still matches
#     # the globally published version
#     return index._state["version"] == latest_version

# # Here we discover eligible campaigns & then try to safely allocate one.
# def make_decision(
#     decision_request: DecisionRequest,
#     db: Session,
#     index: InvertedIndex,
#     campaign_lease_manager: CampaignLeaseManager,
# ) -> Campaign | None:

#     try:
#         decision_time = datetime.now(timezone.utc)

#         # PostgreSQL provides durable campaign eligibility data
#         repository = CampaignRepository(db)

#         # Redis handles shared frequency, idempotency,
#         # and index-version checks
#         allocation_store = AllocationStore(redis_client)

#         # Check whether this request was already allocated previously
#         existing_campaign_id = allocation_store.get_existing_decision(
#             decision_request.request_id
#         )

#         # Return the previous decision without allocating again
#         if existing_campaign_id is not None:
#             return repository.get_by_id(existing_campaign_id)

#         # Maximum two attempts:
#         # initial attempt + one retry if the index became stale
#         for attempt in range(2):

#             # Ensure the local index is current before candidate discovery
#             index_is_ready = ensure_index_current(
#                 index,
#                 repository,
#             )

#             if not index_is_ready:
#                 # Fail closed if the index cannot be refreshed
#                 return None

#             # Discover targeting matches and capture the SAME snapshot version
#             candidate_ids, snapshot_version = index.find_candidates(
#                 decision_request.region,
#                 decision_request.device,
#                 decision_request.subscription_tier,
#             )

#             # PostgreSQL validates campaign status and time window
#             candidates = repository.get_candidates_by_ids(
#                 candidate_ids,
#                 decision_time,
#             )

#             # Track whether Redis rejected an outdated index
#             stale_index = False

#             for candidate_campaign in candidates:

#                 # Before Redis can allocate this campaign,
#                 # this worker must first own one unit of campaign capacity
#                 reservation_result = (
#                     campaign_lease_manager.reserve_one_unit(
#                         candidate_campaign.id
#                     )
#                 )

#                 # This worker cannot acquire any more capacity
#                 # for this campaign
#                 if (
#                     reservation_result
#                     == LeaseReservationResult.NO_UNLEASED_CAPACITY
#                 ):
#                     continue

#                 # PostgreSQL refill failed even after the bounded retry
#                 if (
#                     reservation_result
#                     == LeaseReservationResult.REFILL_FAILED
#                 ):
#                     continue

#                 # At this point, exactly one campaign unit
#                 # has been reserved locally for this request

#                 # Redis now performs the shared atomic checks:
#                 # index version, frequency cap, and request idempotency
#                 result = allocation_store.try_allocate(
#                     candidate_campaign.id,
#                     decision_request.request_id,
#                     decision_request.viewer_id,
#                     candidate_campaign.frequency_cap_per_hour,
#                     decision_time,
#                     snapshot_version,
#                 )

#                 # Another worker may already have allocated
#                 # this exact same request
#                 if result == AllocationResult.ALREADY_PROCESSED:

#                     # This attempt did not consume campaign capacity,
#                     # so give its locally reserved unit back
#                     campaign_lease_manager.refund_one_unit(
#                         candidate_campaign.id
#                     )

#                     existing_campaign_id = (
#                         allocation_store.get_existing_decision(
#                             decision_request.request_id
#                         )
#                     )

#                     return repository.get_by_id(
#                         existing_campaign_id
#                     )

#                 # Campaign configuration changed after candidate discovery
#                 if result == AllocationResult.STALE_INDEX:

#                     # No allocation happened,
#                     # so return the reserved campaign unit
#                     campaign_lease_manager.refund_one_unit(
#                         candidate_campaign.id
#                     )

#                     stale_index = True

#                     # Abandon candidates from this stale snapshot
#                     break

#                 # Successful allocation
#                 if result == AllocationResult.ALLOCATED:

#                     # Do NOT refund.
#                     # The reserved campaign unit is now consumed.
#                     return candidate_campaign

#                 # Any other normal Redis rejection,
#                 # such as frequency capping,
#                 # means this allocation definitely did not happen.
#                 # Therefore the local campaign unit is safe to reuse.
#                 campaign_lease_manager.refund_one_unit(
#                     candidate_campaign.id
#                 )

#                 # Existing loop naturally tries the next campaign

#             # No stale index occurred.
#             # All eligible candidates were attempted without allocation.
#             if not stale_index:
#                 return None

#             # If stale_index is True, the outer loop repeats:
#             # 1. Refresh index
#             # 2. Rediscover candidates
#             # 3. Attempt allocation again

#         # Both attempts exhausted: fail closed
#         return None

#     except RedisError:

#         # IMPORTANT:
#         # If Redis fails after we reserved local capacity,
#         # we do NOT refund that unit.
#         #
#         # Redis may have completed the Lua operation but lost the response.
#         # Burning one unit is safer than accidentally creating capacity.
#         logger.exception(
#             "Redis failure during decision allocation"
#         )

#         return None