import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.models.campaign import Campaign, CampaignLease
from app.repositories.campaign_repository import (
    CampaignLeaseResult,
    CampaignRepository,
)
from app.runtime.campaign_lease_manager import (
    CampaignLeaseManager,
    LeaseReservationResult,
)


def _active_campaign(
    db_session,
    *,
    capacity: int = 10,
    remaining_unleased_capacity: int | None = None,
) -> Campaign:
    now = datetime.now(timezone.utc)

    if remaining_unleased_capacity is None:
        remaining_unleased_capacity = capacity

    campaign = Campaign(
        name="Lease Test Campaign",
        capacity=capacity,
        remaining_unleased_capacity=remaining_unleased_capacity,
        status="ACTIVE",
        frequency_cap_per_hour=3,
        start_time=now - timedelta(hours=1),
        end_time=now + timedelta(hours=1),
        duration_seconds=15
    )

    db_session.add(campaign)
    db_session.commit()
    db_session.refresh(campaign)

    return campaign


def _lease(
    *,
    campaign_id: int,
    granted_units: int,
    requested_units: int | None = None,
) -> CampaignLease:
    if requested_units is None:
        requested_units = granted_units

    return CampaignLease(
        lease_request_id=f"lease-{campaign_id}-{granted_units}-{time.time_ns()}",
        campaign_id=campaign_id,
        worker_id="worker-test",
        requested_units=requested_units,
        granted_units=granted_units,
    )


def test_acquire_lease_deducts_durable_unleased_capacity(db_session):
    campaign = _active_campaign(
        db_session,
        capacity=10,
    )

    repository = CampaignRepository(db_session)

    result = repository.acquire_lease(
        campaign_id=campaign.id,
        worker_id="worker-a",
        lease_request_id="lease-request-1",
        requested_units=4,
    )

    assert isinstance(result, CampaignLease)
    assert result.granted_units == 4

    db_session.commit()
    db_session.refresh(campaign)

    assert campaign.remaining_unleased_capacity == 6


def test_acquire_lease_grants_partial_final_lease(db_session):
    campaign = _active_campaign(
        db_session,
        capacity=10,
        remaining_unleased_capacity=3,
    )

    repository = CampaignRepository(db_session)

    result = repository.acquire_lease(
        campaign_id=campaign.id,
        worker_id="worker-a",
        lease_request_id="lease-request-partial",
        requested_units=10,
    )

    assert isinstance(result, CampaignLease)
    assert result.requested_units == 10
    assert result.granted_units == 3

    db_session.commit()
    db_session.refresh(campaign)

    assert campaign.remaining_unleased_capacity == 0


def test_lease_request_id_is_idempotent(db_session):
    campaign = _active_campaign(
        db_session,
        capacity=10,
    )

    repository = CampaignRepository(db_session)

    first = repository.acquire_lease(
        campaign_id=campaign.id,
        worker_id="worker-a",
        lease_request_id="same-lease-request",
        requested_units=4,
    )

    assert isinstance(first, CampaignLease)

    db_session.commit()
    db_session.refresh(campaign)

    assert campaign.remaining_unleased_capacity == 6

    second = repository.acquire_lease(
        campaign_id=campaign.id,
        worker_id="worker-a",
        lease_request_id="same-lease-request",
        requested_units=4,
    )

    assert isinstance(second, CampaignLease)

    db_session.commit()
    db_session.refresh(campaign)

    assert second.lease_request_id == first.lease_request_id
    assert second.granted_units == 4
    assert campaign.remaining_unleased_capacity == 6


def test_acquire_lease_reports_no_unleased_capacity(db_session):
    campaign = _active_campaign(
        db_session,
        capacity=10,
        remaining_unleased_capacity=0,
    )

    repository = CampaignRepository(db_session)

    result = repository.acquire_lease(
        campaign_id=campaign.id,
        worker_id="worker-a",
        lease_request_id="lease-request-empty",
        requested_units=4,
    )

    assert result == CampaignLeaseResult.NO_UNLEASED_CAPACITY


def test_manager_refills_once_then_spends_from_local_pool():
    manager = CampaignLeaseManager(
        worker_id="worker-a",
        lease_size=3,
    )

    durable_lease = _lease(
        campaign_id=42,
        granted_units=3,
    )

    with patch.object(
        manager,
        "_acquire_durable_lease",
        return_value=durable_lease,
    ) as acquire_mock:

        assert manager.reserve_one_unit(42) == LeaseReservationResult.RESERVED
        assert manager.reserve_one_unit(42) == LeaseReservationResult.RESERVED
        assert manager.reserve_one_unit(42) == LeaseReservationResult.RESERVED

    assert acquire_mock.call_count == 1
    assert manager._get_campaign_state(42).remaining_units == 0


def test_refund_returns_unit_to_worker_local_pool():
    manager = CampaignLeaseManager(
        worker_id="worker-a",
        lease_size=2,
    )

    durable_lease = _lease(
        campaign_id=51,
        granted_units=2,
    )

    with patch.object(
        manager,
        "_acquire_durable_lease",
        return_value=durable_lease,
    ):
        assert manager.reserve_one_unit(51) == LeaseReservationResult.RESERVED

    state = manager._get_campaign_state(51)
    assert state.remaining_units == 1

    manager.refund_one_unit(51)

    assert state.remaining_units == 2


def test_no_unleased_capacity_disables_future_refills_for_that_worker():
    manager = CampaignLeaseManager(
        worker_id="worker-a",
        lease_size=5,
    )

    with patch.object(
        manager,
        "_acquire_durable_lease",
        return_value=CampaignLeaseResult.NO_UNLEASED_CAPACITY,
    ) as acquire_mock:

        first = manager.reserve_one_unit(61)
        second = manager.reserve_one_unit(61)

    assert first == LeaseReservationResult.NO_UNLEASED_CAPACITY
    assert second == LeaseReservationResult.NO_UNLEASED_CAPACITY
    assert acquire_mock.call_count == 1
    assert manager._get_campaign_state(61).no_more_refills is True


def test_refill_failure_is_bounded_to_two_attempts():
    manager = CampaignLeaseManager(
        worker_id="worker-a",
        lease_size=5,
    )

    with patch.object(
        manager,
        "_acquire_durable_lease",
        side_effect=RuntimeError("database unavailable"),
    ) as acquire_mock:

        result = manager.reserve_one_unit(71)

    assert result == LeaseReservationResult.REFILL_FAILED
    assert acquire_mock.call_count == 2


def test_single_flight_allows_only_one_refill_for_concurrent_requests():
    manager = CampaignLeaseManager(
        worker_id="worker-a",
        lease_size=5,
    )

    durable_lease = _lease(
        campaign_id=81,
        granted_units=5,
    )

    def slow_refill(*args, **kwargs):
        time.sleep(0.1)
        return durable_lease

    with patch.object(
        manager,
        "_acquire_durable_lease",
        side_effect=slow_refill,
    ) as acquire_mock:

        with ThreadPoolExecutor(max_workers=5) as executor:
            results = list(
                executor.map(
                    lambda _: manager.reserve_one_unit(81),
                    range(5),
                )
            )

    assert results == [LeaseReservationResult.RESERVED] * 5
    assert acquire_mock.call_count == 1
    assert manager._get_campaign_state(81).remaining_units == 0
