# /Users/vish1504/projectRush/tests/test_decision.py

from datetime import datetime, timezone, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from uuid import uuid4
from unittest.mock import patch

import pytest
from redis.exceptions import RedisError

from app.main import app
from app.models.campaign import Campaign, CampaignRegion
from app.repositories.campaign_repository import CampaignRepository
from app.runtime.campaign_lease_manager import LeaseReservationResult


client = TestClient(app)


def test_matching_campaign_is_returned(db_session):
    now = datetime.now(timezone.utc)

    campaign_input = {
        "name": "Nike Cricket Final",
        "capacity": 100000,
        "frequency_cap_per_hour": 3,
        "duration_seconds": 15,
        "start_time": (now - timedelta(hours=1)).isoformat(),
        "end_time": (now + timedelta(hours=1)).isoformat(),
        "regions": ["Mumbai"],
        "devices": ["MOBILE"],
        "subscription_tiers": ["PREMIUM"],
    }

    create_response = client.post(
        "/campaigns",
        json=campaign_input,
    )

    assert create_response.status_code == 201
    campaign_id = create_response.json()["id"]

    campaign = db_session.get(Campaign, campaign_id)
    campaign.status = "ACTIVE"
    db_session.commit()

    repository = CampaignRepository(db_session)

    candidates = repository.find_candidates(
        region="Mumbai",
        device="MOBILE",
        subscription_tier="PREMIUM",
        decision_time=now,
    )

    assert len(candidates) == 1
    assert candidates[0].id == campaign_id


@pytest.mark.parametrize(
    "decision_request",
    [
        {
            "request_id": "mismatch-request-1",
            "viewer_id": "test-viewer-1",
            "region": "Delhi",
            "device": "MOBILE",
            "subscription_tier": "PREMIUM",
            "break_duration_seconds": 30,
        },
        {
            "request_id": "mismatch-request-2",
            "viewer_id": "test-viewer-1",
            "region": "Mumbai",
            "device": "ANDROID_TV",
            "subscription_tier": "PREMIUM",
            "break_duration_seconds": 30,
        },
        {
            "request_id": "mismatch-request-3",
            "viewer_id": "test-viewer-1",
            "region": "Mumbai",
            "device": "MOBILE",
            "subscription_tier": "FREE",
            "break_duration_seconds": 30,
        },
    ],
)
def test_targeting_mismatch_is_rejected(
    db_session,
    decision_request,
):
    now = datetime.now(timezone.utc)

    campaign_input = {
        "name": "Nike Cricket Final",
        "capacity": 100000,
        "frequency_cap_per_hour": 3,
        "duration_seconds": 15,
        "start_time": (now - timedelta(hours=1)).isoformat(),
        "end_time": (now + timedelta(hours=1)).isoformat(),
        "regions": ["Mumbai"],
        "devices": ["MOBILE"],
        "subscription_tiers": ["PREMIUM"],
    }

    create_response = client.post(
        "/campaigns",
        json=campaign_input,
    )

    campaign_id = create_response.json()["id"]

    campaign = db_session.get(Campaign, campaign_id)
    campaign.status = "ACTIVE"
    db_session.commit()

    repository = CampaignRepository(db_session)

    candidates = repository.find_candidates(
        region=decision_request["region"],
        device=decision_request["device"],
        subscription_tier=decision_request["subscription_tier"],
        decision_time=now,
    )

    assert candidates == []


def test_draft_campaign_is_rejected(db_session):
    now = datetime.now(timezone.utc)

    campaign_input = {
        "name": "Draft Campaign",
        "capacity": 100,
        "frequency_cap_per_hour": 3,
        "duration_seconds": 15,
        "start_time": (now - timedelta(hours=1)).isoformat(),
        "end_time": (now + timedelta(hours=1)).isoformat(),
        "regions": ["Mumbai"],
        "devices": ["MOBILE"],
        "subscription_tiers": ["PREMIUM"],
    }

    response = client.post(
        "/campaigns",
        json=campaign_input,
    )

    assert response.status_code == 201

    repository = CampaignRepository(db_session)

    candidates = repository.find_candidates(
        region="Mumbai",
        device="MOBILE",
        subscription_tier="PREMIUM",
        decision_time=now,
    )

    assert candidates == []


@pytest.mark.parametrize(
    "start_offset,end_offset",
    [
        (1, 2),
        (-2, -1),
    ],
)
def test_campaign_outside_time_window_is_rejected(
    db_session,
    start_offset,
    end_offset,
):
    now = datetime.now(timezone.utc)

    campaign_input = {
        "name": "Time Test Campaign",
        "capacity": 100,
        "frequency_cap_per_hour": 3,
        "duration_seconds": 15,
        "start_time": (
            now + timedelta(hours=start_offset)
        ).isoformat(),
        "end_time": (
            now + timedelta(hours=end_offset)
        ).isoformat(),
        "regions": ["Mumbai"],
        "devices": ["MOBILE"],
        "subscription_tiers": ["PREMIUM"],
    }

    create_response = client.post(
        "/campaigns",
        json=campaign_input,
    )

    assert create_response.status_code == 201

    campaign_id = create_response.json()["id"]

    campaign = db_session.get(Campaign, campaign_id)
    campaign.status = "ACTIVE"
    db_session.commit()

    repository = CampaignRepository(db_session)

    candidates = repository.find_candidates(
        region="Mumbai",
        device="MOBILE",
        subscription_tier="PREMIUM",
        decision_time=now,
    )

    assert candidates == []


def test_campaign_with_no_targeting_rows_is_unrestricted(
    db_session,
):
    now = datetime.now(timezone.utc)

    campaign_input = {
        "name": "Global Campaign",
        "capacity": 100,
        "frequency_cap_per_hour": 3,
        "duration_seconds": 15,
        "start_time": (now - timedelta(hours=1)).isoformat(),
        "end_time": (now + timedelta(hours=1)).isoformat(),
        "regions": [],
        "devices": [],
        "subscription_tiers": [],
    }

    create_response = client.post(
        "/campaigns",
        json=campaign_input,
    )

    assert create_response.status_code == 201

    campaign_id = create_response.json()["id"]

    campaign = db_session.get(Campaign, campaign_id)
    campaign.status = "ACTIVE"
    db_session.commit()

    repository = CampaignRepository(db_session)

    candidates = repository.find_candidates(
        region="Bengaluru",
        device="SOME_RANDOM_DEVICE",
        subscription_tier="FREE",
        decision_time=now,
    )

    assert len(candidates) == 1
    assert candidates[0].id == campaign_id


def test_campaign_at_exact_end_time_is_rejected(db_session):
    start_time = datetime(
        2026,
        10,
        10,
        18,
        0,
        tzinfo=timezone.utc,
    )

    end_time = datetime(
        2026,
        10,
        10,
        20,
        0,
        tzinfo=timezone.utc,
    )

    campaign_input = {
        "name": "Boundary Campaign",
        "capacity": 100,
        "frequency_cap_per_hour": 3,
        "duration_seconds": 15,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "regions": ["Mumbai"],
        "devices": ["MOBILE"],
        "subscription_tiers": ["PREMIUM"],
    }

    create_response = client.post(
        "/campaigns",
        json=campaign_input,
    )

    campaign_id = create_response.json()["id"]

    campaign = db_session.get(Campaign, campaign_id)
    campaign.status = "ACTIVE"
    db_session.commit()

    repository = CampaignRepository(db_session)

    candidates = repository.find_candidates(
        region="Mumbai",
        device="MOBILE",
        subscription_tier="PREMIUM",
        decision_time=end_time,
    )

    assert candidates == []


def test_only_eligible_campaigns_are_returned(db_session):
    now = datetime.now(timezone.utc)

    matching_campaign = {
        "name": "Matching Campaign",
        "capacity": 100,
        "frequency_cap_per_hour": 3,
        "duration_seconds": 15,
        "start_time": (now - timedelta(hours=1)).isoformat(),
        "end_time": (now + timedelta(hours=1)).isoformat(),
        "regions": ["Mumbai"],
        "devices": ["MOBILE"],
        "subscription_tiers": ["PREMIUM"],
    }

    wrong_region_campaign = {
        "name": "Delhi Campaign",
        "capacity": 100,
        "frequency_cap_per_hour": 3,
        "duration_seconds": 15,
        "start_time": (now - timedelta(hours=1)).isoformat(),
        "end_time": (now + timedelta(hours=1)).isoformat(),
        "regions": ["Delhi"],
        "devices": ["MOBILE"],
        "subscription_tiers": ["PREMIUM"],
    }

    unrestricted_campaign = {
        "name": "Global Campaign",
        "capacity": 100,
        "frequency_cap_per_hour": 3,
        "duration_seconds": 15,
        "start_time": (now - timedelta(hours=1)).isoformat(),
        "end_time": (now + timedelta(hours=1)).isoformat(),
        "regions": [],
        "devices": [],
        "subscription_tiers": [],
    }

    responses = [
        client.post(
            "/campaigns",
            json=matching_campaign,
        ),
        client.post(
            "/campaigns",
            json=wrong_region_campaign,
        ),
        client.post(
            "/campaigns",
            json=unrestricted_campaign,
        ),
    ]

    campaign_ids = [
        response.json()["id"]
        for response in responses
    ]

    for campaign_id in campaign_ids:
        campaign = db_session.get(
            Campaign,
            campaign_id,
        )
        campaign.status = "ACTIVE"

    db_session.commit()

    repository = CampaignRepository(db_session)

    candidates = repository.find_candidates(
        region="Mumbai",
        device="MOBILE",
        subscription_tier="PREMIUM",
        decision_time=now,
    )

    returned_ids = {
        campaign.id
        for campaign in candidates
    }

    assert returned_ids == {
        campaign_ids[0],
        campaign_ids[2],
    }


def test_duplicate_targeting_rolls_back_campaign(
    db_session,
):
    now = datetime.now(timezone.utc)

    campaign_input = {
        "name": "Duplicate Target Campaign",
        "capacity": 100,
        "frequency_cap_per_hour": 3,
        "duration_seconds": 15,
        "start_time": (now - timedelta(hours=1)).isoformat(),
        "end_time": (now + timedelta(hours=1)).isoformat(),
        "regions": ["Mumbai", "Mumbai"],
        "devices": ["MOBILE"],
        "subscription_tiers": ["PREMIUM"],
    }

    with pytest.raises(IntegrityError):
        client.post(
            "/campaigns",
            json=campaign_input,
        )

    campaign = db_session.scalar(
        select(Campaign).where(
            Campaign.name
            == "Duplicate Target Campaign"
        )
    )

    assert campaign is None


def test_deleting_campaign_deletes_targeting_rows(
    db_session,
):
    now = datetime.now(timezone.utc)

    campaign_input = {
        "name": "Delete Me",
        "capacity": 100,
        "frequency_cap_per_hour": 3,
        "duration_seconds": 15,
        "start_time": (now - timedelta(hours=1)).isoformat(),
        "end_time": (now + timedelta(hours=1)).isoformat(),
        "regions": ["Mumbai"],
        "devices": ["MOBILE"],
        "subscription_tiers": ["PREMIUM"],
    }

    create_response = client.post(
        "/campaigns",
        json=campaign_input,
    )

    campaign_id = create_response.json()["id"]

    campaign = db_session.get(
        Campaign,
        campaign_id,
    )

    db_session.delete(campaign)
    db_session.commit()

    region = db_session.scalar(
        select(CampaignRegion).where(
            CampaignRegion.campaign_id
            == campaign_id
        )
    )

    assert region is None


def test_decision_allocates_eligible_campaign():
    now = datetime.now(timezone.utc)

    campaign_input = {
        "name": "winner_campaign",
        "capacity": 100,
        "frequency_cap_per_hour": 3,
        "duration_seconds": 15,
        "start_time": (now - timedelta(hours=1)).isoformat(),
        "end_time": (now + timedelta(hours=1)).isoformat(),
        "regions": ["Mumbai"],
        "devices": ["MOBILE"],
        "subscription_tiers": ["PREMIUM"],
    }

    create_response = client.post(
        "/campaigns",
        json=campaign_input,
    )

    assert create_response.status_code == 201

    campaign_id = create_response.json()["id"]

    activate_response = client.post(
        f"/campaigns/{campaign_id}/activate"
    )

    assert activate_response.status_code == 200

    request_id = f"decision-test-{uuid4()}"
    viewer_id = f"viewer-test-{uuid4()}"

    response = client.post(
        "/decisions",
        json={
            "request_id": request_id,
            "viewer_id": viewer_id,
            "region": "Mumbai",
            "device": "MOBILE",
            "subscription_tier": "PREMIUM",
            "break_duration_seconds": 30,
        },
    )

    assert response.status_code == 200

    body = response.json()

    assert body["request_id"] == request_id
    assert body["status"] == "ALLOCATED"

    assert len(body["slots"]) == 1

    assert (
        body["slots"][0]["campaign_id"]
        == campaign_id
    )

    assert (
        body["slots"][0]["source"]
        == "COMMERCIAL"
    )

    assert (
        body["slots"][0]["duration_seconds"]
        == 15
    )

    assert body["filled_duration_seconds"] == 15
    assert body["unfilled_duration_seconds"] == 15

    lease_manager = (
        app.state.campaign_lease_manager
    )

    lease_manager.reserve_one_unit.assert_called_once_with(
        campaign_id
    )

    lease_manager.refund_one_unit.assert_not_called()


def test_decision_returns_no_fill_when_no_campaign_is_eligible():
    request_id = f"no-fill-test-{uuid4()}"
    viewer_id = f"viewer-test-{uuid4()}"

    response = client.post(
        "/decisions",
        json={
            "request_id": request_id,
            "viewer_id": viewer_id,
            "region": "Mumbai",
            "device": "MOBILE",
            "subscription_tier": "PREMIUM",
            "break_duration_seconds": 30,
        },
    )

    assert response.status_code == 200

    body = response.json()

    assert body["request_id"] == request_id
    assert body["status"] == "NO_FILL"
    assert body["slots"] == []
    assert body["filled_duration_seconds"] == 0
    assert body["unfilled_duration_seconds"] == 30

    (
        app.state.campaign_lease_manager
        .reserve_one_unit
        .assert_not_called()
    )


def test_no_fill_is_not_sticky_and_can_succeed_on_retry():
    now = datetime.now(timezone.utc)

    request_id = f"retry-no-fill-{uuid4()}"
    viewer_id = f"viewer-retry-{uuid4()}"

    decision_request = {
        "request_id": request_id,
        "viewer_id": viewer_id,
        "region": "Mumbai",
        "device": "MOBILE",
        "subscription_tier": "PREMIUM",
        "break_duration_seconds": 30,
    }

    first_response = client.post(
        "/decisions",
        json=decision_request,
    )

    assert first_response.status_code == 200

    first_body = first_response.json()

    assert first_body["status"] == "NO_FILL"
    assert first_body["slots"] == []
    assert first_body["filled_duration_seconds"] == 0
    assert first_body["unfilled_duration_seconds"] == 30

    campaign_input = {
        "name": "Retry Campaign",
        "capacity": 100,
        "frequency_cap_per_hour": 3,
        "duration_seconds": 15,
        "start_time": (now - timedelta(hours=1)).isoformat(),
        "end_time": (now + timedelta(hours=1)).isoformat(),
        "regions": ["Mumbai"],
        "devices": ["MOBILE"],
        "subscription_tiers": ["PREMIUM"],
    }

    create_response = client.post(
        "/campaigns",
        json=campaign_input,
    )

    assert create_response.status_code == 201

    campaign_id = create_response.json()["id"]

    activate_response = client.post(
        f"/campaigns/{campaign_id}/activate"
    )

    assert activate_response.status_code == 200

    second_response = client.post(
        "/decisions",
        json=decision_request,
    )

    assert second_response.status_code == 200

    second_body = second_response.json()

    assert second_body["request_id"] == request_id
    assert second_body["status"] == "ALLOCATED"

    assert (
        second_body["slots"][0]["campaign_id"]
        == campaign_id
    )

    assert (
        second_body["slots"][0]["source"]
        == "COMMERCIAL"
    )


def test_decision_returns_no_fill_when_redis_fails():
    request_id = f"redis-failure-{uuid4()}"
    viewer_id = f"viewer-redis-failure-{uuid4()}"

    with patch(
        (
            "app.services.decision_service."
            "AllocationStore.get_existing_pod_decision"
        ),
        side_effect=RedisError(
            "Redis unavailable"
        ),
    ):
        response = client.post(
            "/decisions",
            json={
                "request_id": request_id,
                "viewer_id": viewer_id,
                "region": "Mumbai",
                "device": "MOBILE",
                "subscription_tier": "PREMIUM",
                "break_duration_seconds": 30,
            },
        )

    assert response.status_code == 200

    body = response.json()

    assert body["request_id"] == request_id
    assert body["status"] == "NO_FILL"
    assert body["slots"] == []
    assert body["filled_duration_seconds"] == 0
    assert body["unfilled_duration_seconds"] == 30


def test_duplicate_decision_request_does_not_reserve_twice():
    now = datetime.now(timezone.utc)

    campaign_input = {
        "name": "Duplicate Request Campaign",
        "capacity": 100,
        "frequency_cap_per_hour": 3,
        "duration_seconds": 15,
        "start_time": (now - timedelta(hours=1)).isoformat(),
        "end_time": (now + timedelta(hours=1)).isoformat(),
        "regions": ["Mumbai"],
        "devices": ["MOBILE"],
        "subscription_tiers": ["PREMIUM"],
    }

    create_response = client.post(
        "/campaigns",
        json=campaign_input,
    )

    assert create_response.status_code == 201

    campaign_id = create_response.json()["id"]

    activate_response = client.post(
        f"/campaigns/{campaign_id}/activate"
    )

    assert activate_response.status_code == 200

    request_id = f"duplicate-request-{uuid4()}"
    viewer_id = f"duplicate-viewer-{uuid4()}"

    decision_request = {
        "request_id": request_id,
        "viewer_id": viewer_id,
        "region": "Mumbai",
        "device": "MOBILE",
        "subscription_tier": "PREMIUM",
        "break_duration_seconds": 30,
    }

    first_response = client.post(
        "/decisions",
        json=decision_request,
    )

    second_response = client.post(
        "/decisions",
        json=decision_request,
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200

    first_body = first_response.json()
    second_body = second_response.json()

    assert first_body["status"] == "ALLOCATED"
    assert second_body["status"] == "ALLOCATED"

    assert (
        first_body["slots"][0]["campaign_id"]
        == campaign_id
    )

    assert (
        second_body["slots"][0]["campaign_id"]
        == campaign_id
    )

    assert (
        first_body["slots"][0]["source"]
        == "COMMERCIAL"
    )

    assert (
        second_body["slots"][0]["source"]
        == "COMMERCIAL"
    )

    lease_manager = (
        app.state.campaign_lease_manager
    )

    lease_manager.reserve_one_unit.assert_called_once_with(
        campaign_id
    )


def test_decision_skips_campaign_with_no_unleased_capacity_and_allocates_next(
    db_session,
):
    now = datetime.now(timezone.utc)

    campaign_input = {
        "capacity": 100,
        "frequency_cap_per_hour": 3,
        "duration_seconds": 15,
        "start_time": (now - timedelta(hours=1)).isoformat(),
        "end_time": (now + timedelta(hours=1)).isoformat(),
        "regions": ["Mumbai"],
        "devices": ["MOBILE"],
        "subscription_tiers": ["PREMIUM"],
    }

    first_response = client.post(
        "/campaigns",
        json={
            **campaign_input,
            "name": (
                "No Unleased Capacity Campaign"
            ),
        },
    )

    second_response = client.post(
        "/campaigns",
        json={
            **campaign_input,
            "name": "Healthy Campaign",
        },
    )

    first_id = first_response.json()["id"]
    second_id = second_response.json()["id"]

    assert client.post(
        f"/campaigns/{first_id}/activate"
    ).status_code == 200

    assert client.post(
        f"/campaigns/{second_id}/activate"
    ).status_code == 200

    db_session.expire_all()

    first_campaign = db_session.get(
        Campaign,
        first_id,
    )

    second_campaign = db_session.get(
        Campaign,
        second_id,
    )

    lease_manager = (
        app.state.campaign_lease_manager
    )

    lease_manager.reserve_one_unit.side_effect = [
        LeaseReservationResult.NO_UNLEASED_CAPACITY,
        LeaseReservationResult.RESERVED,
    ]

    request_id = f"fallback-test-{uuid4()}"

    with patch.object(
        CampaignRepository,
        "get_candidates_by_ids",
        return_value=[
            first_campaign,
            second_campaign,
        ],
    ):
        response = client.post(
            "/decisions",
            json={
                "request_id": request_id,
                "viewer_id": (
                    f"viewer-{uuid4()}"
                ),
                "region": "Mumbai",
                "device": "MOBILE",
                "subscription_tier": "PREMIUM",
                "break_duration_seconds": 30,
            },
        )

    assert response.status_code == 200

    body = response.json()

    assert body["status"] == "ALLOCATED"

    assert len(body["slots"]) == 1

    assert (
        body["slots"][0]["campaign_id"]
        == second_id
    )

    assert (
        body["slots"][0]["source"]
        == "COMMERCIAL"
    )

    assert body["filled_duration_seconds"] == 15
    assert body["unfilled_duration_seconds"] == 15

    assert (
        lease_manager.reserve_one_unit.call_count
        == 2
    )

    assert (
        lease_manager
        .reserve_one_unit
        .call_args_list[0]
        .args[0]
        == first_id
    )

    assert (
        lease_manager
        .reserve_one_unit
        .call_args_list[1]
        .args[0]
        == second_id
    )
    
    
    

# from datetime import datetime, timezone, timedelta

# from fastapi.testclient import TestClient
# from sqlalchemy import select
# from sqlalchemy.exc import IntegrityError
# from uuid import uuid4
# from unittest.mock import patch

# import pytest
# from redis.exceptions import RedisError

# from app.main import app
# from app.models.campaign import Campaign, CampaignRegion
# from app.repositories.campaign_repository import CampaignRepository
# from app.runtime.campaign_lease_manager import LeaseReservationResult


# client = TestClient(app)


# def test_matching_campaign_is_returned(db_session):
#     now = datetime.now(timezone.utc)

#     campaign_input = {
#         "name": "Nike Cricket Final",
#         "capacity": 100000,
#         "frequency_cap_per_hour": 3,
#         "start_time": (now - timedelta(hours=1)).isoformat(),
#         "end_time": (now + timedelta(hours=1)).isoformat(),
#         "regions": ["Mumbai"],
#         "devices": ["MOBILE"],
#         "subscription_tiers": ["PREMIUM"],
#         "duration_seconds": 15,
#     }

#     create_response = client.post(
#         "/campaigns",
#         json=campaign_input,
#     )

#     assert create_response.status_code == 201
#     campaign_id = create_response.json()["id"]

#     campaign = db_session.get(Campaign, campaign_id)
#     campaign.status = "ACTIVE"
#     db_session.commit()

#     repository = CampaignRepository(db_session)

#     candidates = repository.find_candidates(
#         region="Mumbai",
#         device="MOBILE",
#         subscription_tier="PREMIUM",
#         decision_time=now,
#     )

#     assert len(candidates) == 1
#     assert candidates[0].id == campaign_id


# @pytest.mark.parametrize(
#     "decision_request",
#     [
#         {
#             "request_id": "mismatch-request-1",
#             "viewer_id": "test-viewer-1",
#             "region": "Delhi",
#             "device": "MOBILE",
#             "subscription_tier": "PREMIUM",
#         },
#         {
#             "request_id": "mismatch-request-2",
#             "viewer_id": "test-viewer-1",
#             "region": "Mumbai",
#             "device": "ANDROID_TV",
#             "subscription_tier": "PREMIUM",
#         },
#         {
#             "request_id": "mismatch-request-3",
#             "viewer_id": "test-viewer-1",
#             "region": "Mumbai",
#             "device": "MOBILE",
#             "subscription_tier": "FREE",
#         },
#     ],
# )
# def test_targeting_mismatch_is_rejected(
#     db_session,
#     decision_request,
# ):
#     now = datetime.now(timezone.utc)

#     campaign_input = {
#         "name": "Nike Cricket Final",
#         "capacity": 100000,
#         "frequency_cap_per_hour": 3,
#         "start_time": (now - timedelta(hours=1)).isoformat(),
#         "end_time": (now + timedelta(hours=1)).isoformat(),
#         "regions": ["Mumbai"],
#         "devices": ["MOBILE"],
#         "subscription_tiers": ["PREMIUM"],
#         "duration_seconds": 15,
#     }

#     create_response = client.post(
#         "/campaigns",
#         json=campaign_input,
#     )

#     campaign_id = create_response.json()["id"]

#     campaign = db_session.get(Campaign, campaign_id)
#     campaign.status = "ACTIVE"
#     db_session.commit()

#     repository = CampaignRepository(db_session)

#     candidates = repository.find_candidates(
#         region=decision_request["region"],
#         device=decision_request["device"],
#         subscription_tier=decision_request["subscription_tier"],
#         decision_time=now,
#     )

#     assert candidates == []


# def test_draft_campaign_is_rejected(db_session):
#     now = datetime.now(timezone.utc)

#     campaign_input = {
#         "name": "Draft Campaign",
#         "capacity": 100,
#         "frequency_cap_per_hour": 3,
#         "start_time": (now - timedelta(hours=1)).isoformat(),
#         "end_time": (now + timedelta(hours=1)).isoformat(),
#         "regions": ["Mumbai"],
#         "devices": ["MOBILE"],
#         "subscription_tiers": ["PREMIUM"],
#         "duration_seconds": 15,
#     }

#     response = client.post(
#         "/campaigns",
#         json=campaign_input,
#     )

#     assert response.status_code == 201

#     repository = CampaignRepository(db_session)

#     candidates = repository.find_candidates(
#         region="Mumbai",
#         device="MOBILE",
#         subscription_tier="PREMIUM",
#         decision_time=now,
#     )

#     assert candidates == []


# @pytest.mark.parametrize(
#     "start_offset,end_offset",
#     [
#         (1, 2),
#         (-2, -1),
#     ],
# )
# def test_campaign_outside_time_window_is_rejected(
#     db_session,
#     start_offset,
#     end_offset,
# ):
#     now = datetime.now(timezone.utc)

#     campaign_input = {
#         "name": "Time Test Campaign",
#         "capacity": 100,
#         "frequency_cap_per_hour": 3,
#         "start_time": (now + timedelta(hours=start_offset)).isoformat(),
#         "end_time": (now + timedelta(hours=end_offset)).isoformat(),
#         "regions": ["Mumbai"],
#         "devices": ["MOBILE"],
#         "subscription_tiers": ["PREMIUM"],
#         "duration_seconds": 15,
#     }

#     create_response = client.post("/campaigns", json=campaign_input)
#     assert create_response.status_code == 201

#     campaign_id = create_response.json()["id"]

#     campaign = db_session.get(Campaign, campaign_id)
#     campaign.status = "ACTIVE"
#     db_session.commit()

#     repository = CampaignRepository(db_session)

#     candidates = repository.find_candidates(
#         region="Mumbai",
#         device="MOBILE",
#         subscription_tier="PREMIUM",
#         decision_time=now,
#     )

#     assert candidates == []


# def test_campaign_with_no_targeting_rows_is_unrestricted(db_session):
#     now = datetime.now(timezone.utc)

#     campaign_input = {
#         "name": "Global Campaign",
#         "capacity": 100,
#         "frequency_cap_per_hour": 3,
#         "start_time": (now - timedelta(hours=1)).isoformat(),
#         "end_time": (now + timedelta(hours=1)).isoformat(),
#         "regions": [],
#         "devices": [],
#         "subscription_tiers": [],
#         "duration_seconds": 15,
#     }

#     create_response = client.post("/campaigns", json=campaign_input)
#     assert create_response.status_code == 201

#     campaign_id = create_response.json()["id"]

#     campaign = db_session.get(Campaign, campaign_id)
#     campaign.status = "ACTIVE"
#     db_session.commit()

#     repository = CampaignRepository(db_session)

#     candidates = repository.find_candidates(
#         region="Bengaluru",
#         device="SOME_RANDOM_DEVICE",
#         subscription_tier="FREE",
#         decision_time=now,
#     )

#     assert len(candidates) == 1
#     assert candidates[0].id == campaign_id


# def test_campaign_at_exact_end_time_is_rejected(db_session):
#     start_time = datetime(2026, 10, 10, 18, 0, tzinfo=timezone.utc)
#     end_time = datetime(2026, 10, 10, 20, 0, tzinfo=timezone.utc)

#     campaign_input = {
#         "name": "Boundary Campaign",
#         "capacity": 100,
#         "frequency_cap_per_hour": 3,
#         "start_time": start_time.isoformat(),
#         "end_time": end_time.isoformat(),
#         "regions": ["Mumbai"],
#         "devices": ["MOBILE"],
#         "subscription_tiers": ["PREMIUM"],
#         "duration_seconds": 15,
#     }

#     create_response = client.post("/campaigns", json=campaign_input)
#     campaign_id = create_response.json()["id"]

#     campaign = db_session.get(Campaign, campaign_id)
#     campaign.status = "ACTIVE"
#     db_session.commit()

#     repository = CampaignRepository(db_session)

#     candidates = repository.find_candidates(
#         region="Mumbai",
#         device="MOBILE",
#         subscription_tier="PREMIUM",
#         decision_time=end_time,
#     )

#     assert candidates == []


# def test_only_eligible_campaigns_are_returned(db_session):
#     now = datetime.now(timezone.utc)

#     matching_campaign = {
#         "name": "Matching Campaign",
#         "capacity": 100,
#         "frequency_cap_per_hour": 3,
#         "start_time": (now - timedelta(hours=1)).isoformat(),
#         "end_time": (now + timedelta(hours=1)).isoformat(),
#         "regions": ["Mumbai"],
#         "devices": ["MOBILE"],
#         "subscription_tiers": ["PREMIUM"],
#         "duration_seconds": 15,
#     }

#     wrong_region_campaign = {
#         "name": "Delhi Campaign",
#         "capacity": 100,
#         "frequency_cap_per_hour": 3,
#         "start_time": (now - timedelta(hours=1)).isoformat(),
#         "end_time": (now + timedelta(hours=1)).isoformat(),
#         "regions": ["Delhi"],
#         "devices": ["MOBILE"],
#         "subscription_tiers": ["PREMIUM"],
#         "duration_seconds": 15,
#     }

#     unrestricted_campaign = {
#         "name": "Global Campaign",
#         "capacity": 100,
#         "frequency_cap_per_hour": 3,
#         "start_time": (now - timedelta(hours=1)).isoformat(),
#         "end_time": (now + timedelta(hours=1)).isoformat(),
#         "regions": [],
#         "devices": [],
#         "subscription_tiers": [],
#         "duration_seconds": 15,
#     }

#     responses = [
#         client.post("/campaigns", json=matching_campaign),
#         client.post("/campaigns", json=wrong_region_campaign),
#         client.post("/campaigns", json=unrestricted_campaign),
#     ]

#     campaign_ids = [response.json()["id"] for response in responses]

#     for campaign_id in campaign_ids:
#         campaign = db_session.get(Campaign, campaign_id)
#         campaign.status = "ACTIVE"

#     db_session.commit()

#     repository = CampaignRepository(db_session)

#     candidates = repository.find_candidates(
#         region="Mumbai",
#         device="MOBILE",
#         subscription_tier="PREMIUM",
#         decision_time=now,
#     )

#     returned_ids = {campaign.id for campaign in candidates}

#     assert returned_ids == {
#         campaign_ids[0],
#         campaign_ids[2],
#     }


# def test_duplicate_targeting_rolls_back_campaign(db_session):
#     now = datetime.now(timezone.utc)

#     campaign_input = {
#         "name": "Duplicate Target Campaign",
#         "capacity": 100,
#         "frequency_cap_per_hour": 3,
#         "start_time": (now - timedelta(hours=1)).isoformat(),
#         "end_time": (now + timedelta(hours=1)).isoformat(),
#         "regions": ["Mumbai", "Mumbai"],
#         "devices": ["MOBILE"],
#         "subscription_tiers": ["PREMIUM"],
#         "duration_seconds": 15,
#     }

#     with pytest.raises(IntegrityError):
#         client.post(
#             "/campaigns",
#             json=campaign_input,
#         )

#     campaign = db_session.scalar(
#         select(Campaign).where(
#             Campaign.name == "Duplicate Target Campaign"
#         )
#     )

#     assert campaign is None


# def test_deleting_campaign_deletes_targeting_rows(db_session):
#     now = datetime.now(timezone.utc)

#     campaign_input = {
#         "name": "Delete Me",
#         "capacity": 100,
#         "frequency_cap_per_hour": 3,
#         "start_time": (now - timedelta(hours=1)).isoformat(),
#         "end_time": (now + timedelta(hours=1)).isoformat(),
#         "regions": ["Mumbai"],
#         "devices": ["MOBILE"],
#         "subscription_tiers": ["PREMIUM"],
#         "duration_seconds": 15,
#     }

#     create_response = client.post("/campaigns", json=campaign_input)
#     campaign_id = create_response.json()["id"]

#     campaign = db_session.get(Campaign, campaign_id)

#     db_session.delete(campaign)
#     db_session.commit()

#     region = db_session.scalar(
#         select(CampaignRegion).where(
#             CampaignRegion.campaign_id == campaign_id
#         )
#     )

#     assert region is None


# def test_decision_allocates_eligible_campaign():
#     now = datetime.now(timezone.utc)

#     campaign_input = {
#         "name": "winner_campaign",
#         "capacity": 100,
#         "frequency_cap_per_hour": 3,
#         "duration_seconds": 15,
#         "start_time": (now - timedelta(hours=1)).isoformat(),
#         "end_time": (now + timedelta(hours=1)).isoformat(),
#         "regions": ["Mumbai"],
#         "devices": ["MOBILE"],
#         "subscription_tiers": ["PREMIUM"],
        
#     }

#     create_response = client.post(
#         "/campaigns",
#         json=campaign_input,
#     )

#     assert create_response.status_code == 201
#     campaign_id = create_response.json()["id"]

#     activate_response = client.post(
#         f"/campaigns/{campaign_id}/activate"
#     )
#     assert activate_response.status_code == 200

#     request_id = f"decision-test-{uuid4()}"
#     viewer_id = f"viewer-test-{uuid4()}"

#     response = client.post(
#         "/decisions",
#         json={
#             "request_id": request_id,
#             "viewer_id": viewer_id,
#             "region": "Mumbai",
#             "device": "MOBILE",
#             "subscription_tier": "PREMIUM",
#             "break_duration_seconds": 30,
#         },
#     )

#     assert response.status_code == 200

#     body = response.json()

#     assert body["request_id"] == request_id
#     assert body["status"] == "ALLOCATED"
#     assert body["campaign_id"] == campaign_id

#     lease_manager = app.state.campaign_lease_manager
#     lease_manager.reserve_one_unit.assert_called_once_with(campaign_id)
#     lease_manager.refund_one_unit.assert_not_called()


# def test_decision_returns_no_fill_when_no_campaign_is_eligible():
#     request_id = f"no-fill-test-{uuid4()}"
#     viewer_id = f"viewer-test-{uuid4()}"

#     response = client.post(
#         "/decisions",
#         json={
#             "request_id": request_id,
#             "viewer_id": viewer_id,
#             "region": "Mumbai",
#             "device": "MOBILE",
#             "subscription_tier": "PREMIUM",
#             "break_duration_seconds": 30,
#         },
#     )

#     assert response.status_code == 200

#     body = response.json()

#     assert body["request_id"] == request_id
#     assert body["status"] == "NO_FILL"
#     assert body["campaign_id"] is None

#     app.state.campaign_lease_manager.reserve_one_unit.assert_not_called()


# def test_no_fill_is_not_sticky_and_can_succeed_on_retry():
#     now = datetime.now(timezone.utc)

#     request_id = f"retry-no-fill-{uuid4()}"
#     viewer_id = f"viewer-retry-{uuid4()}"

#     decision_request = {
#         "request_id": request_id,
#         "viewer_id": viewer_id,
#         "region": "Mumbai",
#         "device": "MOBILE",
#         "subscription_tier": "PREMIUM",
#     }

#     first_response = client.post(
#         "/decisions",
#         json=decision_request,
#     )

#     assert first_response.status_code == 200
#     assert first_response.json()["status"] == "NO_FILL"
#     assert first_response.json()["campaign_id"] is None

#     campaign_input = {
#         "name": "Retry Campaign",
#         "capacity": 100,
#         "frequency_cap_per_hour": 3,
#         "start_time": (now - timedelta(hours=1)).isoformat(),
#         "end_time": (now + timedelta(hours=1)).isoformat(),
#         "regions": ["Mumbai"],
#         "devices": ["MOBILE"],
#         "subscription_tiers": ["PREMIUM"],
#     }

#     create_response = client.post(
#         "/campaigns",
#         json=campaign_input,
#     )

#     assert create_response.status_code == 201
#     campaign_id = create_response.json()["id"]

#     activate_response = client.post(
#         f"/campaigns/{campaign_id}/activate"
#     )
#     assert activate_response.status_code == 200

#     second_response = client.post(
#         "/decisions",
#         json=decision_request,
#     )

#     assert second_response.status_code == 200
#     assert second_response.json()["request_id"] == request_id
#     assert second_response.json()["status"] == "ALLOCATED"
#     assert second_response.json()["campaign_id"] == campaign_id


# def test_decision_returns_no_fill_when_redis_fails():
#     request_id = f"redis-failure-{uuid4()}"
#     viewer_id = f"viewer-redis-failure-{uuid4()}"

#     with patch(
#         "app.services.decision_service.AllocationStore.get_existing_decision",
#         side_effect=RedisError("Redis unavailable"),
#     ):
#         response = client.post(
#             "/decisions",
#             json={
#                 "request_id": request_id,
#                 "viewer_id": viewer_id,
#                 "region": "Mumbai",
#                 "device": "MOBILE",
#                 "subscription_tier": "PREMIUM",
#                 "break_duration_seconds": 30,
#             },
#         )

#     assert response.status_code == 200
#     assert response.json()["request_id"] == request_id
#     assert response.json()["status"] == "NO_FILL"
#     assert response.json()["campaign_id"] is None


# def test_duplicate_decision_request_does_not_reserve_twice():
#     now = datetime.now(timezone.utc)

#     campaign_input = {
#         "name": "Duplicate Request Campaign",
#         "capacity": 100,
#         "frequency_cap_per_hour": 3,
#         "start_time": (now - timedelta(hours=1)).isoformat(),
#         "end_time": (now + timedelta(hours=1)).isoformat(),
#         "regions": ["Mumbai"],
#         "devices": ["MOBILE"],
#         "subscription_tiers": ["PREMIUM"],
#     }

#     create_response = client.post(
#         "/campaigns",
#         json=campaign_input,
#     )

#     assert create_response.status_code == 201
#     campaign_id = create_response.json()["id"]

#     activate_response = client.post(
#         f"/campaigns/{campaign_id}/activate"
#     )
#     assert activate_response.status_code == 200

#     request_id = f"duplicate-request-{uuid4()}"
#     viewer_id = f"duplicate-viewer-{uuid4()}"

#     decision_request = {
#         "request_id": request_id,
#         "viewer_id": viewer_id,
#         "region": "Mumbai",
#         "device": "MOBILE",
#         "subscription_tier": "PREMIUM",
#     }

#     first_response = client.post(
#         "/decisions",
#         json=decision_request,
#     )

#     second_response = client.post(
#         "/decisions",
#         json=decision_request,
#     )

#     assert first_response.status_code == 200
#     assert first_response.json()["status"] == "ALLOCATED"
#     assert first_response.json()["campaign_id"] == campaign_id

#     assert second_response.status_code == 200
#     assert second_response.json()["status"] == "ALLOCATED"
#     assert second_response.json()["campaign_id"] == campaign_id

#     lease_manager = app.state.campaign_lease_manager
#     lease_manager.reserve_one_unit.assert_called_once_with(campaign_id)


# def test_decision_skips_campaign_with_no_unleased_capacity_and_allocates_next(
#     db_session,
# ):
#     now = datetime.now(timezone.utc)

#     campaign_input = {
#         "capacity": 100,
#         "frequency_cap_per_hour": 3,
#         "start_time": (now - timedelta(hours=1)).isoformat(),
#         "end_time": (now + timedelta(hours=1)).isoformat(),
#         "regions": ["Mumbai"],
#         "devices": ["MOBILE"],
#         "subscription_tiers": ["PREMIUM"],
#     }

#     first_response = client.post(
#         "/campaigns",
#         json={
#             **campaign_input,
#             "name": "No Unleased Capacity Campaign",
#         },
#     )

#     second_response = client.post(
#         "/campaigns",
#         json={
#             **campaign_input,
#             "name": "Healthy Campaign",
#         },
#     )

#     first_id = first_response.json()["id"]
#     second_id = second_response.json()["id"]

#     assert client.post(
#         f"/campaigns/{first_id}/activate"
#     ).status_code == 200

#     assert client.post(
#         f"/campaigns/{second_id}/activate"
#     ).status_code == 200

#     db_session.expire_all()
#     first_campaign = db_session.get(Campaign, first_id)
#     second_campaign = db_session.get(Campaign, second_id)

#     lease_manager = app.state.campaign_lease_manager
#     lease_manager.reserve_one_unit.side_effect = [
#         LeaseReservationResult.NO_UNLEASED_CAPACITY,
#         LeaseReservationResult.RESERVED,
#     ]

#     request_id = f"fallback-test-{uuid4()}"

#     with patch.object(
#         CampaignRepository,
#         "get_candidates_by_ids",
#         return_value=[first_campaign, second_campaign],
#     ):
#         response = client.post(
#             "/decisions",
#             json={
#                 "request_id": request_id,
#                 "viewer_id": f"viewer-{uuid4()}",
#                 "region": "Mumbai",
#                 "device": "MOBILE",
#                 "subscription_tier": "PREMIUM",
#                 "break_duration_seconds": 30,
#             },
#         )

#     assert response.status_code == 200
#     assert response.json()["status"] == "ALLOCATED"
#     assert response.json()["campaign_id"] == second_id

#     assert lease_manager.reserve_one_unit.call_count == 2
#     assert lease_manager.reserve_one_unit.call_args_list[0].args[0] == first_id
#     assert lease_manager.reserve_one_unit.call_args_list[1].args[0] == second_id

