from unittest.mock import MagicMock, patch

from redis.exceptions import RedisError

from app.runtime.allocation_store import AllocationResult
from app.runtime.campaign_lease_manager import LeaseReservationResult
from app.services.decision_service import make_decision


def _request():
    request = MagicMock()
    request.request_id = "retry-test"
    request.viewer_id = "viewer-test"
    request.region = "Mumbai"
    request.device = "MOBILE"
    request.subscription_tier = "PREMIUM"
    return request


def test_stale_index_triggers_retry_and_refunds_first_reservation():
    campaign_12 = MagicMock()
    campaign_12.id = 12
    campaign_12.frequency_cap_per_hour = 3

    campaign_15 = MagicMock()
    campaign_15.id = 15
    campaign_15.frequency_cap_per_hour = 3

    db = MagicMock()
    index = MagicMock()
    repository = MagicMock()
    allocation_store = MagicMock()
    campaign_lease_manager = MagicMock()

    index.find_candidates.side_effect = [
        ({12}, 2),
        ({15}, 3),
    ]

    repository.get_candidates_by_ids.side_effect = [
        [campaign_12],
        [campaign_15],
    ]

    allocation_store.get_existing_decision.return_value = None
    allocation_store.try_allocate.side_effect = [
        AllocationResult.STALE_INDEX,
        AllocationResult.ALLOCATED,
    ]

    campaign_lease_manager.reserve_one_unit.return_value = (
        LeaseReservationResult.RESERVED
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
        ) as refresh_mock,
    ):
        result = make_decision(
            _request(),
            db,
            index,
            campaign_lease_manager,
        )

    assert result is campaign_15
    assert refresh_mock.call_count == 2
    assert index.find_candidates.call_count == 2
    assert repository.get_candidates_by_ids.call_count == 2
    assert allocation_store.try_allocate.call_count == 2

    campaign_lease_manager.refund_one_unit.assert_called_once_with(12)


def test_retry_is_bounded():
    db = MagicMock()
    index = MagicMock()
    repository = MagicMock()
    allocation_store = MagicMock()
    campaign_lease_manager = MagicMock()

    campaign = MagicMock()
    campaign.id = 12
    campaign.frequency_cap_per_hour = 3

    index.find_candidates.side_effect = [
        ({12}, 2),
        ({12}, 3),
    ]

    repository.get_candidates_by_ids.return_value = [campaign]
    allocation_store.get_existing_decision.return_value = None
    allocation_store.try_allocate.return_value = AllocationResult.STALE_INDEX

    campaign_lease_manager.reserve_one_unit.return_value = (
        LeaseReservationResult.RESERVED
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
    ):
        result = make_decision(
            _request(),
            db,
            index,
            campaign_lease_manager,
        )

    assert result is None
    assert index.find_candidates.call_count == 2
    assert allocation_store.try_allocate.call_count == 2
    assert campaign_lease_manager.reserve_one_unit.call_count == 2
    assert campaign_lease_manager.refund_one_unit.call_count == 2


def test_frequency_rejection_refunds_reserved_unit():
    db = MagicMock()
    index = MagicMock()
    repository = MagicMock()
    allocation_store = MagicMock()
    campaign_lease_manager = MagicMock()

    campaign = MagicMock()
    campaign.id = 21
    campaign.frequency_cap_per_hour = 3

    index.find_candidates.return_value = ({21}, 7)
    repository.get_candidates_by_ids.return_value = [campaign]

    allocation_store.get_existing_decision.return_value = None
    allocation_store.try_allocate.return_value = (
        AllocationResult.FREQUENCY_CAPPED
    )

    campaign_lease_manager.reserve_one_unit.return_value = (
        LeaseReservationResult.RESERVED
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
    ):
        result = make_decision(
            _request(),
            db,
            index,
            campaign_lease_manager,
        )

    assert result is None
    campaign_lease_manager.refund_one_unit.assert_called_once_with(21)


def test_redis_failure_after_reservation_burns_unit_instead_of_refunding():
    db = MagicMock()
    index = MagicMock()
    repository = MagicMock()
    allocation_store = MagicMock()
    campaign_lease_manager = MagicMock()

    campaign = MagicMock()
    campaign.id = 31
    campaign.frequency_cap_per_hour = 3

    index.find_candidates.return_value = ({31}, 9)
    repository.get_candidates_by_ids.return_value = [campaign]

    allocation_store.get_existing_decision.return_value = None
    allocation_store.try_allocate.side_effect = RedisError(
        "ambiguous Redis failure"
    )

    campaign_lease_manager.reserve_one_unit.return_value = (
        LeaseReservationResult.RESERVED
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
    ):
        result = make_decision(
            _request(),
            db,
            index,
            campaign_lease_manager,
        )

    assert result is None
    campaign_lease_manager.refund_one_unit.assert_not_called()



# from unittest.mock import MagicMock, patch

# from app.runtime.allocation_store import AllocationResult
# from app.services.decision_service import make_decision


# def test_stale_index_triggers_retry():
#     # Simulate a campaign being discovered after a refresh.
#     campaign_12 = MagicMock()
#     campaign_12.id = 12
#     campaign_12.frequency_cap_per_hour = 3

#     campaign_15 = MagicMock()
#     campaign_15.id = 15
#     campaign_15.frequency_cap_per_hour = 3

#     # No actual database or index needed.
#     db = MagicMock()
#     index = MagicMock()
#     repository = MagicMock()
#     allocation_store = MagicMock()

#     # First discovery uses version 2.
#     # Second discovery uses version 3 with different candidates.
#     index.find_candidates.side_effect = [
#         ({12}, 2),
#         ({15}, 3),
#     ]

#     repository.get_candidates_by_ids.side_effect = [
#         [campaign_12],
#         [campaign_15],
#     ]

#     # First allocation is rejected as stale.
#     # Second allocation succeeds.
#     allocation_store.try_allocate.side_effect = [
#         AllocationResult.STALE_INDEX,
#         AllocationResult.ALLOCATED,
#     ]

#     allocation_store.get_existing_decision.return_value = None

#     with (
#         patch(
#             "app.services.decision_service.CampaignRepository",
#             return_value=repository,
#         ),
#         patch(
#             "app.services.decision_service.AllocationStore",
#             return_value=allocation_store,
#         ),
#         patch(
#             "app.services.decision_service.ensure_index_current",
#             return_value=True,
#         ) as refresh_mock,
#     ):
#         request = MagicMock()
#         request.request_id = "retry-test-001"
#         request.viewer_id = "viewer-001"
#         request.region = "Mumbai"
#         request.device = "Mobile"
#         request.subscription_tier = "Premium"

#         result = make_decision(request, db, index)

#     assert result is campaign_15

#     # Freshness was checked on both attempts.
#     assert refresh_mock.call_count == 2

#     # Candidate discovery happened twice.
#     assert index.find_candidates.call_count == 2

#     # PostgreSQL candidate retrieval happened twice.
#     assert repository.get_candidates_by_ids.call_count == 2

#     # Allocation was attempted twice.
#     assert allocation_store.try_allocate.call_count == 2

#     # Verify we did not retry with the original campaign.
#     assert allocation_store.try_allocate.call_args_list[0].args[0] == 12
#     assert allocation_store.try_allocate.call_args_list[0].args[5] == 2

#     assert allocation_store.try_allocate.call_args_list[1].args[0] == 15
#     assert allocation_store.try_allocate.call_args_list[1].args[5] == 3

#     print("Stale index refresh-and-retry test passed!")


# def test_retry_is_bounded():
#     db = MagicMock()
#     index = MagicMock()
#     repository = MagicMock()
#     allocation_store = MagicMock()

#     campaign = MagicMock()
#     campaign.id = 12
#     campaign.frequency_cap_per_hour = 3

#     index.find_candidates.side_effect = [
#         ({12}, 2),
#         ({12}, 3),
#     ]

#     repository.get_candidates_by_ids.return_value = [campaign]

#     # Even the second attempt encounters a stale version.
#     allocation_store.try_allocate.return_value = AllocationResult.STALE_INDEX
#     allocation_store.get_existing_decision.return_value = None

#     with (
#         patch(
#             "app.services.decision_service.CampaignRepository",
#             return_value=repository,
#         ),
#         patch(
#             "app.services.decision_service.AllocationStore",
#             return_value=allocation_store,
#         ),
#         patch(
#             "app.services.decision_service.ensure_index_current",
#             return_value=True,
#         ),
#     ):
#         request = MagicMock()
#         request.request_id = "retry-test-002"
#         request.viewer_id = "viewer-002"
#         request.region = "Mumbai"
#         request.device = "Mobile"
#         request.subscription_tier = "Premium"

#         result = make_decision(request, db, index)

#     # Fail closed rather than retry indefinitely.
#     assert result is None

#     assert index.find_candidates.call_count == 2
#     assert allocation_store.try_allocate.call_count == 2

#     print("Bounded retry test passed!")


# if __name__ == "__main__":
#     test_stale_index_triggers_retry()
#     test_retry_is_bounded()
#     print("All decision retry tests passed!")
