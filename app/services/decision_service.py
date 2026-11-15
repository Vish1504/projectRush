# # /Users/vish1504/projectRush/app/services/decision_service.py


from datetime import datetime, timezone
import logging
import os
import json
import time
from contextlib import contextmanager

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

    # Retrieve the latest index version from Redis.
    redis_version_raw = redis_client.get(
        "rush:index:version"
    )

    if redis_version_raw is None:
        return False

    redis_version = int(redis_version_raw)

    # Fast path: no rebuild or locking is required
    # if this worker already has the latest version.
    if index._state["version"] == redis_version:
        return True

    # Only one request per worker can rebuild the index.
    with index.rebuild_lock:

        # Another request may have already rebuilt the index
        # while this request was waiting for the lock.
        redis_version_raw = redis_client.get(
            "rush:index:version"
        )

        if redis_version_raw is None:
            return False

        redis_version = int(redis_version_raw)

        # Recheck the version after acquiring the lock.
        if index._state["version"] == redis_version:
            return True

        try:

            # Rebuild the index using the latest campaign data.
            repository.load_index(
                index,
                version=redis_version,
            )

        finally:

            # Release the database connection before releasing
            # the lock so waiting requests don't hold up the pool.
            repository.db.rollback()

        # Check whether the version changed during the rebuild.
        latest_version_raw = redis_client.get(
            "rush:index:version"
        )

        if latest_version_raw is None:
            return False

        latest_version = int(latest_version_raw)

        # Refuse to use the index if it is already outdated.
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


# --------------------------------------------------
# Decision profiling
# --------------------------------------------------

# Enable profiling only when explicitly requested.
_PROFILE_ENABLED = os.getenv("RUSH_PROFILE") == "1"


class _DecisionProfile:

    def __init__(self):

        # Stores the time spent in each stage of the request.
        self.timings = {}

        # Record when the decision service starts processing.
        self.started = (
            time.perf_counter()
            if _PROFILE_ENABLED
            else 0
        )

    @contextmanager
    def stage(self, name):

        # Skip timing measurements if profiling is disabled.
        if not _PROFILE_ENABLED:
            yield
            return

        # Start the timer before executing this stage.
        started = time.perf_counter()

        try:
            yield

        finally:

            # Calculate the elapsed time in milliseconds.
            elapsed = (
                time.perf_counter() - started
            ) * 1000

            # Add previous timings in case a stage is retried.
            self.timings[name] = (
                self.timings.get(name, 0) + elapsed
            )

    def finish(self, request_id):

        if _PROFILE_ENABLED:

            # Build one profiling record for the entire request.
            record = {
                "request_id": request_id,
                "worker_pid": os.getpid(),
                "total_ms": (
                    time.perf_counter() - self.started
                ) * 1000,
                "stages": self.timings,
            }

            # Warning level ensures the record appears in server logs.
            logger.warning(
                "RUSH_PROFILE %s",
                json.dumps(record),
            )


# --------------------------------------------------
# Public decision service
# --------------------------------------------------

def make_decision(
    decision_request: DecisionRequest,
    db: Session,
    index: InvertedIndex,
    campaign_lease_manager: CampaignLeaseManager,
) -> DecisionResponse:

    # Create a separate profiler for each decision request.
    profile = _DecisionProfile()

    try:

        # Execute the existing decision logic.
        return _make_decision(
            decision_request,
            db,
            index,
            campaign_lease_manager,
            profile,
        )

    finally:

        # Record timings even if the request returns early or fails.
        profile.finish(
            decision_request.request_id
        )


# --------------------------------------------------
# Actual decision logic
# --------------------------------------------------

def _make_decision(
    decision_request: DecisionRequest,
    db: Session,
    index: InvertedIndex,
    campaign_lease_manager: CampaignLeaseManager,
    profile: _DecisionProfile,
) -> DecisionResponse:

    decision_time = datetime.now(timezone.utc)

    repository = CampaignRepository(db)
    allocation_store = AllocationStore(redis_client)

    try:

        # Fast path for a request Redis has already committed.
        with profile.stage("idempotency_lookup"):

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

            # Measure index version checking and rebuilding.
            with profile.stage("index_sync"):

                if not ensure_index_current(
                    index,
                    repository,
                ):
                    return _no_fill_response(
                        decision_request
                    )

            # Measure bitmap-based candidate identification.
            with profile.stage("bitmap_lookup"):

                candidate_ids, snapshot_version = (
                    index.find_candidates(
                        decision_request.region,
                        decision_request.device,
                        decision_request.subscription_tier,
                    )
                )

            # Measure the PostgreSQL candidate query.
            with profile.stage("candidate_retrieval"):

                candidates = (
                    repository.get_candidates_by_ids(
                        candidate_ids,
                        decision_time,
                    )
                )

            # Release the connection before we proceed to leasing.
            with profile.stage("connection_release"):

                # Detach the campaign objects while retaining their data.
                db.expunge_all()

                # End the read transaction & return the connection.
                db.rollback()

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

            # Measure the Redis read for committed allocation counts.
            with profile.stage("pacing_counter_read"):

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

        # Measure the time spent calculating pacing scores.
        with profile.stage("pacing_calculation"):

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

        # Measure how long the optimizer takes to construct a pod.
        with profile.stage("pod_optimization"):

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
        with profile.stage("capacity_reservation"):

            # Includes local reservations, lease refills and waiting.
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

        # Partial fill is allowed, so failed reservations are dropped.
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

            # Measure the atomic Redis pod commit.
            with profile.stage("allocation_commit"):

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
# import logging

# from redis.exceptions import RedisError
# from sqlalchemy.orm import Session

# from app.models.campaign import Campaign
# from app.repositories.campaign_repository import CampaignRepository
# from app.runtime.allocation_store import (
#     AllocationStore,
#     PodCommitCandidate,
#     PodCommitSlot,
#     PodCommitStatus,
#     PodSlotCommitStatus,
# )
# from app.runtime.campaign_lease_manager import (
#     CampaignLeaseManager,
#     LeaseReservationResult,
# )
# from app.runtime.redis_client import redis_client
# from app.schemas.decision import (
#     DecisionRequest,
#     DecisionResponse,
#     DecisionStatusEnum,
#     PodSlot,
#     PodSlotSource,
# )
# from app.services.inverted_index import InvertedIndex
# from app.services.pacing import calculate_pacing_score
# from app.services.pod_optimizer import build_pod


# logger = logging.getLogger(__name__)


# # Makes sure this worker is using the latest campaign index.
# def ensure_index_current(
#     index: InvertedIndex,
#     repository: CampaignRepository,
# ) -> bool:

#     redis_version_raw = redis_client.get(
#         "rush:index:version"
#     )

#     if redis_version_raw is None:
#         return False

#     redis_version = int(redis_version_raw)

#     if index._state["version"] == redis_version:
#         return True

#     repository.load_index(
#         index,
#         version=redis_version,
#     )

#     latest_version_raw = redis_client.get(
#         "rush:index:version"
#     )

#     if latest_version_raw is None:
#         return False

#     latest_version = int(latest_version_raw)

#     return (
#         index._state["version"]
#         == latest_version
#     )


# def _no_fill_response(
#     request: DecisionRequest,
# ) -> DecisionResponse:

#     return DecisionResponse(
#         request_id=request.request_id,
#         status=DecisionStatusEnum.NO_FILL,
#         slots=[],
#         filled_duration_seconds=0,
#         unfilled_duration_seconds=(
#             request.break_duration_seconds
#         ),
#     )


# # Builds the final response from Redis's committed pod result.
# def _build_committed_response(
#     request: DecisionRequest,
#     committed_slots: tuple[PodCommitSlot, ...],
# ) -> DecisionResponse:

#     response_slots = []

#     has_commercial = False

#     for slot in committed_slots:

#         if slot.status == PodSlotCommitStatus.ALLOCATED:
#             has_commercial = True

#             response_slots.append(
#                 PodSlot(
#                     campaign_id=slot.campaign_id,
#                     duration_seconds=slot.duration_seconds,
#                     source=PodSlotSource.COMMERCIAL,
#                 )
#             )

#         else:
#             # Replace a rejected commercial slot with House.
#             response_slots.append(
#                 PodSlot(
#                     campaign_id=None,
#                     duration_seconds=slot.duration_seconds,
#                     source=PodSlotSource.HOUSE,
#                 )
#             )

#     filled_duration = sum(
#         slot.duration_seconds
#         for slot in response_slots
#     )

#     if not response_slots:
#         status = DecisionStatusEnum.NO_FILL

#     elif has_commercial:
#         status = DecisionStatusEnum.ALLOCATED

#     else:
#         status = DecisionStatusEnum.DEGRADED_FALLBACK

#     return DecisionResponse(
#         request_id=request.request_id,
#         status=status,
#         slots=response_slots,
#         filled_duration_seconds=filled_duration,
#         unfilled_duration_seconds=max(
#             0,
#             request.break_duration_seconds
#             - filled_duration,
#         ),
#     )


# # Used when Redis may have committed but we lost the result.
# def _build_ambiguous_fallback(
#     request: DecisionRequest,
#     reserved_campaigns: list[Campaign],
# ) -> DecisionResponse:

#     slots = []

#     for campaign in reserved_campaigns:
#         slots.append(
#             PodSlot(
#                 campaign_id=None,
#                 duration_seconds=campaign.duration_seconds,
#                 source=PodSlotSource.HOUSE,
#             )
#         )

#     filled_duration = sum(
#         slot.duration_seconds
#         for slot in slots
#     )

#     return DecisionResponse(
#         request_id=request.request_id,
#         status=DecisionStatusEnum.DEGRADED_FALLBACK,
#         slots=slots,
#         filled_duration_seconds=filled_duration,
#         unfilled_duration_seconds=max(
#             0,
#             request.break_duration_seconds
#             - filled_duration,
#         ),
#     )


# def make_decision(
#     decision_request: DecisionRequest,
#     db: Session,
#     index: InvertedIndex,
#     campaign_lease_manager: CampaignLeaseManager,
# ) -> DecisionResponse:

#     decision_time = datetime.now(timezone.utc)

#     repository = CampaignRepository(db)
#     allocation_store = AllocationStore(redis_client)

#     try:
#         # Fast path for a request Redis has already committed.
#         existing_slots = (
#             allocation_store.get_existing_pod_decision(
#                 decision_request.request_id
#             )
#         )

#         if existing_slots is not None:
#             return _build_committed_response(
#                 decision_request,
#                 existing_slots,
#             )

#     except RedisError:
#         logger.exception(
#             "Redis failure while checking existing decision"
#         )

#         return _no_fill_response(
#             decision_request
#         )

#     # Initial attempt plus one stale-index retry.
#     for attempt in range(2):

#         try:
#             if not ensure_index_current(
#                 index,
#                 repository,
#             ):
#                 return _no_fill_response(
#                     decision_request
#                 )

#             candidate_ids, snapshot_version = (
#                 index.find_candidates(
#                     decision_request.region,
#                     decision_request.device,
#                     decision_request.subscription_tier,
#                 )
#             )

#             candidates = (
#                 repository.get_candidates_by_ids(
#                     candidate_ids,
#                     decision_time,
#                 )
#             )
            
#             # Detach the campaign objects while retaining their loaded data.
#             db.expunge_all()

#             # End the read transaction & return the connection to the pool.
#             db.rollback()
            
#             # Campaigns longer than the break cannot be used.
#             candidates = [
#                 campaign
#                 for campaign in candidates
#                 if campaign.duration_seconds
#                 <= decision_request.break_duration_seconds
#             ]

#             if not candidates:
#                 return _no_fill_response(
#                     decision_request
#                 )

#             campaign_ids = [
#                 campaign.id
#                 for campaign in candidates
#             ]

#             committed_counts = (
#                 allocation_store
#                 .get_committed_allocation_counts(
#                     campaign_ids
#                 )
#             )

#         except RedisError:
#             logger.exception(
#                 "Redis failure before pod construction"
#             )

#             return _no_fill_response(
#                 decision_request
#             )

#         # Pacing only changes preference, never eligibility.
#         pacing_scores = {}

#         for campaign in candidates:
#             pacing_scores[campaign.id] = (
#                 calculate_pacing_score(
#                     campaign=campaign,
#                     committed_allocations=(
#                         committed_counts[campaign.id]
#                     ),
#                     now=decision_time,
#                 )
#             )

#         proposed_pod = build_pod(
#             candidates=candidates,
#             break_duration_seconds=(
#                 decision_request.break_duration_seconds
#             ),
#             campaign_pacing_scores=pacing_scores,
#         )

#         if not proposed_pod:
#             return _no_fill_response(
#                 decision_request
#             )

#         reserved_campaigns = []

#         # Only campaigns with local capacity can reach Redis.
#         for campaign in proposed_pod:

#             reservation_result = (
#                 campaign_lease_manager.reserve_one_unit(
#                     campaign.id
#                 )
#             )

#             if (
#                 reservation_result
#                 == LeaseReservationResult.RESERVED
#             ):
#                 reserved_campaigns.append(
#                     campaign
#                 )

#         # Partial fill is allowed, so failed reservations are just dropped.
#         if not reserved_campaigns:
#             return _no_fill_response(
#                 decision_request
#             )

#         commit_candidates = [
#             PodCommitCandidate(
#                 campaign_id=campaign.id,
#                 duration_seconds=campaign.duration_seconds,
#                 frequency_cap_per_hour=(
#                     campaign.frequency_cap_per_hour
#                 ),
#             )
#             for campaign in reserved_campaigns
#         ]

#         try:
#             commit_result = allocation_store.commit_pod(
#                 candidates=commit_candidates,
#                 request_id=decision_request.request_id,
#                 viewer_id=decision_request.viewer_id,
#                 decision_time=decision_time,
#                 index_version=snapshot_version,
#             )

#         except RedisError:
#             # Redis may have committed before the response was lost.
#             # Do not refund uncertain campaign capacity.
#             logger.exception(
#                 "Redis failure during pod commit"
#             )

#             return _build_ambiguous_fallback(
#                 decision_request,
#                 reserved_campaigns,
#             )

#         if (
#             commit_result.status
#             == PodCommitStatus.STALE_INDEX
#         ):
#             # Redis definitely committed nothing.
#             for campaign in reserved_campaigns:
#                 campaign_lease_manager.refund_one_unit(
#                     campaign.id
#                 )

#             # Try once more using a fresh index.
#             continue

#         if (
#             commit_result.status
#             == PodCommitStatus.ALREADY_PROCESSED
#         ):
#             # These new reservations were not consumed.
#             for campaign in reserved_campaigns:
#                 campaign_lease_manager.refund_one_unit(
#                     campaign.id
#                 )

#             return _build_committed_response(
#                 decision_request,
#                 commit_result.slots,
#             )

#         # Refund only campaigns Redis definitely rejected.
#         for slot in commit_result.slots:

#             if (
#                 slot.status
#                 == PodSlotCommitStatus.FREQUENCY_CAPPED
#             ):
#                 campaign_lease_manager.refund_one_unit(
#                     slot.campaign_id
#                 )

#         return _build_committed_response(
#             decision_request,
#             commit_result.slots,
#         )

#     # Both index attempts failed safely.
#     return _no_fill_response(
#         decision_request
#     )
