from datetime import datetime, timezone

from app.runtime.allocation_store import (
    AllocationStore,
    PodCommitCandidate,
    PodCommitStatus,
    PodSlotCommitStatus,
)
from app.runtime.redis_client import redis_client


def publish_index_version(version: int = 1) -> int:
    redis_client.set("rush:index:version", version)
    return version


def cleanup_pod(
    store: AllocationStore,
    candidates: list[PodCommitCandidate],
    viewer_id: str,
    request_id: str,
    decision_time: datetime,
):
    keys = [
        store._pod_decision_key(request_id),
    ]

    for candidate in candidates:
        keys.append(
            store._frequency_key(
                candidate.campaign_id,
                viewer_id,
                decision_time,
            )
        )

        keys.append(
            store._pacing_key(
                candidate.campaign_id
            )
        )

    redis_client.delete(*keys)


def test_commit_pod_allocates_all_valid_campaigns():
    store = AllocationStore(redis_client)
    index_version = publish_index_version()

    viewer_id = "viewer-pod-success"
    request_id = "request-pod-success"

    decision_time = datetime(
        2026,
        11,
        11,
        1,
        30,
        tzinfo=timezone.utc,
    )

    candidates = [
        PodCommitCandidate(
            campaign_id=101,
            duration_seconds=15,
            frequency_cap_per_hour=3,
        ),
        PodCommitCandidate(
            campaign_id=102,
            duration_seconds=10,
            frequency_cap_per_hour=3,
        ),
        PodCommitCandidate(
            campaign_id=103,
            duration_seconds=5,
            frequency_cap_per_hour=3,
        ),
    ]

    cleanup_pod(
        store,
        candidates,
        viewer_id,
        request_id,
        decision_time,
    )

    result = store.commit_pod(
        candidates=candidates,
        request_id=request_id,
        viewer_id=viewer_id,
        decision_time=decision_time,
        index_version=index_version,
    )

    assert result.status == PodCommitStatus.COMMITTED

    assert [
        slot.status
        for slot in result.slots
    ] == [
        PodSlotCommitStatus.ALLOCATED,
        PodSlotCommitStatus.ALLOCATED,
        PodSlotCommitStatus.ALLOCATED,
    ]

    for candidate in candidates:
        frequency_key = store._frequency_key(
            candidate.campaign_id,
            viewer_id,
            decision_time,
        )

        pacing_key = store._pacing_key(
            candidate.campaign_id
        )

        assert redis_client.get(frequency_key) == "1"
        assert redis_client.get(pacing_key) == "1"

    cleanup_pod(
        store,
        candidates,
        viewer_id,
        request_id,
        decision_time,
    )


def test_frequency_capped_campaign_is_rejected_without_affecting_others():
    store = AllocationStore(redis_client)
    index_version = publish_index_version()

    viewer_id = "viewer-pod-frequency"
    request_id = "request-pod-frequency"

    decision_time = datetime(
        2026,
        11,
        11,
        1,
        30,
        tzinfo=timezone.utc,
    )

    nike = PodCommitCandidate(
        campaign_id=201,
        duration_seconds=15,
        frequency_cap_per_hour=3,
    )

    samsung = PodCommitCandidate(
        campaign_id=202,
        duration_seconds=10,
        frequency_cap_per_hour=1,
    )

    swiggy = PodCommitCandidate(
        campaign_id=203,
        duration_seconds=5,
        frequency_cap_per_hour=3,
    )

    candidates = [
        nike,
        samsung,
        swiggy,
    ]

    cleanup_pod(
        store,
        candidates,
        viewer_id,
        request_id,
        decision_time,
    )

    # Samsung has already hit its cap.
    samsung_frequency_key = store._frequency_key(
        samsung.campaign_id,
        viewer_id,
        decision_time,
    )

    redis_client.set(
        samsung_frequency_key,
        1,
    )

    result = store.commit_pod(
        candidates=candidates,
        request_id=request_id,
        viewer_id=viewer_id,
        decision_time=decision_time,
        index_version=index_version,
    )

    assert result.status == PodCommitStatus.COMMITTED

    statuses = {
        slot.campaign_id: slot.status
        for slot in result.slots
    }

    assert statuses[201] == PodSlotCommitStatus.ALLOCATED
    assert statuses[202] == PodSlotCommitStatus.FREQUENCY_CAPPED
    assert statuses[203] == PodSlotCommitStatus.ALLOCATED

    # Samsung must not be incremented again.
    assert redis_client.get(
        samsung_frequency_key
    ) == "1"

    # A rejected campaign must not count toward pacing.
    assert redis_client.get(
        store._pacing_key(202)
    ) is None

    # The other campaigns should commit normally.
    assert redis_client.get(
        store._pacing_key(201)
    ) == "1"

    assert redis_client.get(
        store._pacing_key(203)
    ) == "1"

    cleanup_pod(
        store,
        candidates,
        viewer_id,
        request_id,
        decision_time,
    )


def test_duplicate_pod_request_does_not_apply_effects_twice():
    store = AllocationStore(redis_client)
    index_version = publish_index_version()

    viewer_id = "viewer-pod-duplicate"
    request_id = "request-pod-duplicate"

    decision_time = datetime(
        2026,
        11,
        11,
        1,
        30,
        tzinfo=timezone.utc,
    )

    candidates = [
        PodCommitCandidate(
            campaign_id=301,
            duration_seconds=15,
            frequency_cap_per_hour=3,
        ),
        PodCommitCandidate(
            campaign_id=302,
            duration_seconds=15,
            frequency_cap_per_hour=3,
        ),
    ]

    cleanup_pod(
        store,
        candidates,
        viewer_id,
        request_id,
        decision_time,
    )

    first_result = store.commit_pod(
        candidates=candidates,
        request_id=request_id,
        viewer_id=viewer_id,
        decision_time=decision_time,
        index_version=index_version,
    )

    second_result = store.commit_pod(
        candidates=candidates,
        request_id=request_id,
        viewer_id=viewer_id,
        decision_time=decision_time,
        index_version=index_version,
    )

    assert first_result.status == PodCommitStatus.COMMITTED
    assert second_result.status == PodCommitStatus.ALREADY_PROCESSED

    assert first_result.slots == second_result.slots

    for candidate in candidates:
        frequency_key = store._frequency_key(
            candidate.campaign_id,
            viewer_id,
            decision_time,
        )

        pacing_key = store._pacing_key(
            candidate.campaign_id
        )

        # Retry must not increment either counter twice.
        assert redis_client.get(frequency_key) == "1"
        assert redis_client.get(pacing_key) == "1"

    cleanup_pod(
        store,
        candidates,
        viewer_id,
        request_id,
        decision_time,
    )


def test_stale_index_rejects_whole_pod_without_mutations():
    store = AllocationStore(redis_client)

    redis_client.set(
        "rush:index:version",
        10,
    )

    viewer_id = "viewer-pod-stale"
    request_id = "request-pod-stale"

    decision_time = datetime(
        2026,
        11,
        11,
        1,
        30,
        tzinfo=timezone.utc,
    )

    candidates = [
        PodCommitCandidate(
            campaign_id=401,
            duration_seconds=15,
            frequency_cap_per_hour=3,
        ),
        PodCommitCandidate(
            campaign_id=402,
            duration_seconds=15,
            frequency_cap_per_hour=3,
        ),
    ]

    cleanup_pod(
        store,
        candidates,
        viewer_id,
        request_id,
        decision_time,
    )

    result = store.commit_pod(
        candidates=candidates,
        request_id=request_id,
        viewer_id=viewer_id,
        decision_time=decision_time,

        # Worker thinks version 9 is current.
        index_version=9,
    )

    assert result.status == PodCommitStatus.STALE_INDEX
    assert result.slots == ()

    for candidate in candidates:
        assert redis_client.get(
            store._frequency_key(
                candidate.campaign_id,
                viewer_id,
                decision_time,
            )
        ) is None

        assert redis_client.get(
            store._pacing_key(
                candidate.campaign_id
            )
        ) is None

    assert redis_client.get(
        store._pod_decision_key(request_id)
    ) is None

    cleanup_pod(
        store,
        candidates,
        viewer_id,
        request_id,
        decision_time,
    )


def test_get_committed_allocation_counts_returns_all_campaigns():
    store = AllocationStore(redis_client)

    campaign_ids = [
        501,
        502,
        503,
    ]

    for campaign_id in campaign_ids:
        redis_client.delete(
            store._pacing_key(campaign_id)
        )

    redis_client.set(
        store._pacing_key(501),
        25,
    )

    redis_client.set(
        store._pacing_key(502),
        80,
    )

    counts = store.get_committed_allocation_counts(
        campaign_ids
    )

    assert counts == {
        501: 25,
        502: 80,
        503: 0,
    }

    for campaign_id in campaign_ids:
        redis_client.delete(
            store._pacing_key(campaign_id)
        )