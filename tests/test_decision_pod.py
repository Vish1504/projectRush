from unittest.mock import MagicMock, patch

from redis.exceptions import RedisError

from app.runtime.allocation_store import (
    PodCommitResult,
    PodCommitSlot,
    PodCommitStatus,
    PodSlotCommitStatus,
)
from app.runtime.campaign_lease_manager import (
    LeaseReservationResult,
)
from app.schemas.decision import (
    DecisionRequest,
    DecisionStatusEnum,
    PodSlotSource,
)
from app.services.decision_service import make_decision


def make_request() -> DecisionRequest:
    return DecisionRequest(
        request_id="request-001",
        viewer_id="viewer-001",
        region="Mumbai",
        device="MOBILE",
        subscription_tier="PREMIUM",
        break_duration_seconds=30,
    )


def make_campaign(
    campaign_id: int,
    duration_seconds: int,
):
    campaign = MagicMock()
    campaign.id = campaign_id
    campaign.duration_seconds = duration_seconds
    campaign.frequency_cap_per_hour = 3

    return campaign


def test_decision_commits_complete_pod():
    db = MagicMock()
    index = MagicMock()
    lease_manager = MagicMock()
    repository = MagicMock()
    allocation_store = MagicMock()

    nike = make_campaign(1, 15)
    samsung = make_campaign(2, 15)

    index.find_candidates.return_value = (
        {1, 2},
        7,
    )

    repository.get_candidates_by_ids.return_value = [
        nike,
        samsung,
    ]

    allocation_store.get_existing_pod_decision.return_value = None

    allocation_store.get_committed_allocation_counts.return_value = {
        1: 100,
        2: 200,
    }

    lease_manager.reserve_one_unit.return_value = (
        LeaseReservationResult.RESERVED
    )

    allocation_store.commit_pod.return_value = (
        PodCommitResult(
            status=PodCommitStatus.COMMITTED,
            slots=(
                PodCommitSlot(
                    campaign_id=1,
                    duration_seconds=15,
                    status=PodSlotCommitStatus.ALLOCATED,
                ),
                PodCommitSlot(
                    campaign_id=2,
                    duration_seconds=15,
                    status=PodSlotCommitStatus.ALLOCATED,
                ),
            ),
        )
    )

    with (
        patch(
            "app.services.decision_service.CampaignRepository",
            return_value=repository,
        ),
        patch(
            "app.services.decision_service.AllocationStore",
            return_value=allocation_store,
        ),
        patch(
            "app.services.decision_service.ensure_index_current",
            return_value=True,
        ),
        patch(
            "app.services.decision_service.calculate_pacing_score",
            return_value=0.0,
        ),
        patch(
            "app.services.decision_service.build_pod",
            return_value=[nike, samsung],
        ),
    ):
        result = make_decision(
            make_request(),
            db,
            index,
            lease_manager,
        )

    assert result.status == DecisionStatusEnum.ALLOCATED

    assert [
        slot.campaign_id
        for slot in result.slots
    ] == [1, 2]

    assert result.filled_duration_seconds == 30
    assert result.unfilled_duration_seconds == 0

    assert lease_manager.refund_one_unit.call_count == 0


def test_frequency_capped_campaign_is_refunded_and_replaced_with_house():
    db = MagicMock()
    index = MagicMock()
    lease_manager = MagicMock()
    repository = MagicMock()
    allocation_store = MagicMock()

    nike = make_campaign(1, 15)
    samsung = make_campaign(2, 15)

    index.find_candidates.return_value = (
        {1, 2},
        7,
    )

    repository.get_candidates_by_ids.return_value = [
        nike,
        samsung,
    ]

    allocation_store.get_existing_pod_decision.return_value = None

    allocation_store.get_committed_allocation_counts.return_value = {
        1: 0,
        2: 0,
    }

    lease_manager.reserve_one_unit.return_value = (
        LeaseReservationResult.RESERVED
    )

    allocation_store.commit_pod.return_value = (
        PodCommitResult(
            status=PodCommitStatus.COMMITTED,
            slots=(
                PodCommitSlot(
                    campaign_id=1,
                    duration_seconds=15,
                    status=PodSlotCommitStatus.ALLOCATED,
                ),
                PodCommitSlot(
                    campaign_id=2,
                    duration_seconds=15,
                    status=PodSlotCommitStatus.FREQUENCY_CAPPED,
                ),
            ),
        )
    )

    with (
        patch(
            "app.services.decision_service.CampaignRepository",
            return_value=repository,
        ),
        patch(
            "app.services.decision_service.AllocationStore",
            return_value=allocation_store,
        ),
        patch(
            "app.services.decision_service.ensure_index_current",
            return_value=True,
        ),
        patch(
            "app.services.decision_service.calculate_pacing_score",
            return_value=0.0,
        ),
        patch(
            "app.services.decision_service.build_pod",
            return_value=[nike, samsung],
        ),
    ):
        result = make_decision(
            make_request(),
            db,
            index,
            lease_manager,
        )

    assert result.status == DecisionStatusEnum.ALLOCATED

    assert result.slots[0].source == PodSlotSource.COMMERCIAL
    assert result.slots[0].campaign_id == 1

    assert result.slots[1].source == PodSlotSource.HOUSE
    assert result.slots[1].campaign_id is None

    # Samsung never committed, so its local unit is safe to reuse.
    lease_manager.refund_one_unit.assert_called_once_with(2)


def test_campaign_without_local_capacity_is_dropped_from_pod():
    db = MagicMock()
    index = MagicMock()
    lease_manager = MagicMock()
    repository = MagicMock()
    allocation_store = MagicMock()

    nike = make_campaign(1, 15)
    samsung = make_campaign(2, 15)

    index.find_candidates.return_value = (
        {1, 2},
        7,
    )

    repository.get_candidates_by_ids.return_value = [
        nike,
        samsung,
    ]

    allocation_store.get_existing_pod_decision.return_value = None

    allocation_store.get_committed_allocation_counts.return_value = {
        1: 0,
        2: 0,
    }

    lease_manager.reserve_one_unit.side_effect = [
        LeaseReservationResult.NO_UNLEASED_CAPACITY,
        LeaseReservationResult.RESERVED,
    ]

    allocation_store.commit_pod.return_value = (
        PodCommitResult(
            status=PodCommitStatus.COMMITTED,
            slots=(
                PodCommitSlot(
                    campaign_id=2,
                    duration_seconds=15,
                    status=PodSlotCommitStatus.ALLOCATED,
                ),
            ),
        )
    )

    with (
        patch(
            "app.services.decision_service.CampaignRepository",
            return_value=repository,
        ),
        patch(
            "app.services.decision_service.AllocationStore",
            return_value=allocation_store,
        ),
        patch(
            "app.services.decision_service.ensure_index_current",
            return_value=True,
        ),
        patch(
            "app.services.decision_service.calculate_pacing_score",
            return_value=0.0,
        ),
        patch(
            "app.services.decision_service.build_pod",
            return_value=[nike, samsung],
        ),
    ):
        result = make_decision(
            make_request(),
            db,
            index,
            lease_manager,
        )

    commit_candidates = (
        allocation_store.commit_pod
        .call_args
        .kwargs["candidates"]
    )

    # Nike never reached Redis because no capacity could be reserved.
    assert [
        candidate.campaign_id
        for candidate in commit_candidates
    ] == [2]

    assert result.filled_duration_seconds == 15
    assert result.unfilled_duration_seconds == 15


def test_duplicate_request_replays_existing_pod_without_reserving_capacity():
    db = MagicMock()
    index = MagicMock()
    lease_manager = MagicMock()
    repository = MagicMock()
    allocation_store = MagicMock()

    allocation_store.get_existing_pod_decision.return_value = (
        PodCommitSlot(
            campaign_id=1,
            duration_seconds=15,
            status=PodSlotCommitStatus.ALLOCATED,
        ),
        PodCommitSlot(
            campaign_id=2,
            duration_seconds=15,
            status=PodSlotCommitStatus.ALLOCATED,
        ),
    )

    with (
        patch(
            "app.services.decision_service.CampaignRepository",
            return_value=repository,
        ),
        patch(
            "app.services.decision_service.AllocationStore",
            return_value=allocation_store,
        ),
    ):
        result = make_decision(
            make_request(),
            db,
            index,
            lease_manager,
        )

    assert result.status == DecisionStatusEnum.ALLOCATED
    assert result.filled_duration_seconds == 30

    # Replay must not consume fresh capacity.
    lease_manager.reserve_one_unit.assert_not_called()

    # Replay should not need candidate discovery either.
    index.find_candidates.assert_not_called()


def test_stale_index_refunds_reserved_capacity_and_retries():
    db = MagicMock()
    index = MagicMock()
    lease_manager = MagicMock()
    repository = MagicMock()
    allocation_store = MagicMock()

    nike = make_campaign(1, 15)
    samsung = make_campaign(2, 15)

    index.find_candidates.side_effect = [
        ({1}, 7),
        ({2}, 8),
    ]

    repository.get_candidates_by_ids.side_effect = [
        [nike],
        [samsung],
    ]

    allocation_store.get_existing_pod_decision.return_value = None

    allocation_store.get_committed_allocation_counts.side_effect = [
        {1: 0},
        {2: 0},
    ]

    lease_manager.reserve_one_unit.return_value = (
        LeaseReservationResult.RESERVED
    )

    allocation_store.commit_pod.side_effect = [
        PodCommitResult(
            status=PodCommitStatus.STALE_INDEX,
            slots=(),
        ),
        PodCommitResult(
            status=PodCommitStatus.COMMITTED,
            slots=(
                PodCommitSlot(
                    campaign_id=2,
                    duration_seconds=15,
                    status=PodSlotCommitStatus.ALLOCATED,
                ),
            ),
        ),
    ]

    with (
        patch(
            "app.services.decision_service.CampaignRepository",
            return_value=repository,
        ),
        patch(
            "app.services.decision_service.AllocationStore",
            return_value=allocation_store,
        ),
        patch(
            "app.services.decision_service.ensure_index_current",
            return_value=True,
        ),
        patch(
            "app.services.decision_service.calculate_pacing_score",
            return_value=0.0,
        ),
        patch(
            "app.services.decision_service.build_pod",
            side_effect=[
                [nike],
                [samsung],
            ],
        ),
    ):
        result = make_decision(
            make_request(),
            db,
            index,
            lease_manager,
        )

    # Nike's stale attempt definitely committed nothing.
    lease_manager.refund_one_unit.assert_called_once_with(1)

    assert allocation_store.commit_pod.call_count == 2

    assert result.slots[0].campaign_id == 2
    assert result.slots[0].source == PodSlotSource.COMMERCIAL


def test_redis_failure_during_commit_burns_capacity_and_returns_house():
    db = MagicMock()
    index = MagicMock()
    lease_manager = MagicMock()
    repository = MagicMock()
    allocation_store = MagicMock()

    nike = make_campaign(1, 15)
    samsung = make_campaign(2, 15)

    index.find_candidates.return_value = (
        {1, 2},
        7,
    )

    repository.get_candidates_by_ids.return_value = [
        nike,
        samsung,
    ]

    allocation_store.get_existing_pod_decision.return_value = None

    allocation_store.get_committed_allocation_counts.return_value = {
        1: 0,
        2: 0,
    }

    lease_manager.reserve_one_unit.return_value = (
        LeaseReservationResult.RESERVED
    )

    allocation_store.commit_pod.side_effect = RedisError(
        "connection lost"
    )

    with (
        patch(
            "app.services.decision_service.CampaignRepository",
            return_value=repository,
        ),
        patch(
            "app.services.decision_service.AllocationStore",
            return_value=allocation_store,
        ),
        patch(
            "app.services.decision_service.ensure_index_current",
            return_value=True,
        ),
        patch(
            "app.services.decision_service.calculate_pacing_score",
            return_value=0.0,
        ),
        patch(
            "app.services.decision_service.build_pod",
            return_value=[nike, samsung],
        ),
    ):
        result = make_decision(
            make_request(),
            db,
            index,
            lease_manager,
        )

    assert result.status == DecisionStatusEnum.DEGRADED_FALLBACK

    assert all(
        slot.source == PodSlotSource.HOUSE
        for slot in result.slots
    )

    # Redis may have committed before the response was lost.
    # Refunding here could recreate capacity, so we burn it.
    lease_manager.refund_one_unit.assert_not_called()
    
    
def test_stale_index_retry_is_bounded():
    db = MagicMock()
    index = MagicMock()
    lease_manager = MagicMock()
    repository = MagicMock()
    allocation_store = MagicMock()

    campaign = make_campaign(1, 15)

    index.find_candidates.side_effect = [
        ({1}, 7),
        ({1}, 8),
    ]

    repository.get_candidates_by_ids.return_value = [
        campaign
    ]

    allocation_store.get_existing_pod_decision.return_value = None

    allocation_store.get_committed_allocation_counts.return_value = {
        1: 0
    }

    lease_manager.reserve_one_unit.return_value = (
        LeaseReservationResult.RESERVED
    )

    allocation_store.commit_pod.return_value = (
        PodCommitResult(
            status=PodCommitStatus.STALE_INDEX,
            slots=(),
        )
    )

    with (
        patch(
            "app.services.decision_service.CampaignRepository",
            return_value=repository,
        ),
        patch(
            "app.services.decision_service.AllocationStore",
            return_value=allocation_store,
        ),
        patch(
            "app.services.decision_service.ensure_index_current",
            return_value=True,
        ),
        patch(
            "app.services.decision_service.calculate_pacing_score",
            return_value=0.0,
        ),
        patch(
            "app.services.decision_service.build_pod",
            return_value=[campaign],
        ),
    ):
        result = make_decision(
            make_request(),
            db,
            index,
            lease_manager,
        )

    assert allocation_store.commit_pod.call_count == 2
    assert lease_manager.refund_one_unit.call_count == 2

    assert result.status == DecisionStatusEnum.NO_FILL
    
def test_stale_index_retry_is_bounded():
    db = MagicMock()
    index = MagicMock()
    lease_manager = MagicMock()
    repository = MagicMock()
    allocation_store = MagicMock()

    campaign = make_campaign(1, 15)

    index.find_candidates.side_effect = [
        ({1}, 7),
        ({1}, 8),
    ]

    repository.get_candidates_by_ids.return_value = [
        campaign
    ]

    allocation_store.get_existing_pod_decision.return_value = None

    allocation_store.get_committed_allocation_counts.return_value = {
        1: 0
    }

    lease_manager.reserve_one_unit.return_value = (
        LeaseReservationResult.RESERVED
    )

    allocation_store.commit_pod.return_value = (
        PodCommitResult(
            status=PodCommitStatus.STALE_INDEX,
            slots=(),
        )
    )

    with (
        patch(
            "app.services.decision_service.CampaignRepository",
            return_value=repository,
        ),
        patch(
            "app.services.decision_service.AllocationStore",
            return_value=allocation_store,
        ),
        patch(
            "app.services.decision_service.ensure_index_current",
            return_value=True,
        ),
        patch(
            "app.services.decision_service.calculate_pacing_score",
            return_value=0.0,
        ),
        patch(
            "app.services.decision_service.build_pod",
            return_value=[campaign],
        ),
    ):
        result = make_decision(
            make_request(),
            db,
            index,
            lease_manager,
        )

    assert allocation_store.commit_pod.call_count == 2
    assert lease_manager.refund_one_unit.call_count == 2
    assert result.status == DecisionStatusEnum.NO_FILL