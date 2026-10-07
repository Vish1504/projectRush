# /Users/vish1504/projectRush/tests/test_decision.py
from datetime import datetime, timezone, timedelta

from fastapi.testclient import TestClient

from app.main import app
from app.models.campaign import Campaign, CampaignRegion
import pytest

from sqlalchemy import select

from app.repositories.campaign_repository import CampaignRepository

from sqlalchemy.exc import IntegrityError

from uuid import uuid4

from app.runtime.allocation_store import AllocationStore
from app.runtime.redis_client import redis_client

from unittest.mock import patch
from redis.exceptions import RedisError


client = TestClient(app)


def test_matching_campaign_is_returned(db_session):
    now = datetime.now(timezone.utc)

    campaign_input = {
        "name": "Nike Cricket Final",
        "capacity": 100000,
        "frequency_cap_per_hour": 3,
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
        },
        {
            "request_id": "mismatch-request-2",
            "viewer_id": "test-viewer-1",
            "region": "Mumbai",
            "device": "ANDROID_TV",
            "subscription_tier": "PREMIUM",
        },
        {
            "request_id": "mismatch-request-3",
            "viewer_id": "test-viewer-1",
            "region": "Mumbai",
            "device": "MOBILE",
            "subscription_tier": "FREE",
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

    # Deliberately leave the campaign as DRAFT.

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
        (1, 2),     # starts in the future
        (-2, -1),   # already expired
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
        "start_time": (now + timedelta(hours=start_offset)).isoformat(),
        "end_time": (now + timedelta(hours=end_offset)).isoformat(),
        "regions": ["Mumbai"],
        "devices": ["MOBILE"],
        "subscription_tiers": ["PREMIUM"],
    }

    create_response = client.post("/campaigns", json=campaign_input)
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
    
def test_campaign_with_no_targeting_rows_is_unrestricted(db_session):
    now = datetime.now(timezone.utc)

    campaign_input = {
        "name": "Global Campaign",
        "capacity": 100,
        "frequency_cap_per_hour": 3,
        "start_time": (now - timedelta(hours=1)).isoformat(),
        "end_time": (now + timedelta(hours=1)).isoformat(),
        "regions": [],
        "devices": [],
        "subscription_tiers": [],
    }

    create_response = client.post("/campaigns", json=campaign_input)
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
    start_time = datetime(2026, 10, 10, 18, 0, tzinfo=timezone.utc)
    end_time = datetime(2026, 10, 10, 20, 0, tzinfo=timezone.utc)

    campaign_input = {
        "name": "Boundary Campaign",
        "capacity": 100,
        "frequency_cap_per_hour": 3,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "regions": ["Mumbai"],
        "devices": ["MOBILE"],
        "subscription_tiers": ["PREMIUM"],
    }

    create_response = client.post("/campaigns", json=campaign_input)
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
        "start_time": (now - timedelta(hours=1)).isoformat(),
        "end_time": (now + timedelta(hours=1)).isoformat(),
        "regions": [],
        "devices": [],
        "subscription_tiers": [],
    }

    responses = [
        client.post("/campaigns", json=matching_campaign),
        client.post("/campaigns", json=wrong_region_campaign),
        client.post("/campaigns", json=unrestricted_campaign),
    ]

    campaign_ids = [response.json()["id"] for response in responses]

    for campaign_id in campaign_ids:
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

    returned_ids = {
        campaign.id
        for campaign in candidates
    }

    assert returned_ids == {
        campaign_ids[0],
        campaign_ids[2],
    }
    
    
def test_duplicate_targeting_rolls_back_campaign(db_session):
    now = datetime.now(timezone.utc)

    campaign_input = {
        "name": "Duplicate Target Campaign",
        "capacity": 100,
        "frequency_cap_per_hour": 3,
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
            Campaign.name == "Duplicate Target Campaign"
        )
    )

    assert campaign is None
    
    
def test_deleting_campaign_deletes_targeting_rows(db_session):
    now = datetime.now(timezone.utc)

    campaign_input = {
        "name": "Delete Me",
        "capacity": 100,
        "frequency_cap_per_hour": 3,
        "start_time": (now - timedelta(hours=1)).isoformat(),
        "end_time": (now + timedelta(hours=1)).isoformat(),
        "regions": ["Mumbai"],
        "devices": ["MOBILE"],
        "subscription_tiers": ["PREMIUM"],
    }

    create_response = client.post("/campaigns", json=campaign_input)
    campaign_id = create_response.json()["id"]

    campaign = db_session.get(Campaign, campaign_id)

    db_session.delete(campaign)
    db_session.commit()

    region = db_session.scalar(
        select(CampaignRegion).where(
            CampaignRegion.campaign_id == campaign_id
        )
    )

    assert region is None
    

def test_decision_allocates_eligible_campaign(db_session):
    now = datetime.now(timezone.utc)

    campaign_input = {
        "name": "winner_campaign",
        "capacity": 100,
        "frequency_cap_per_hour": 3,
        "start_time": (now - timedelta(hours=1)).isoformat(),
        "end_time": (now + timedelta(hours=1)).isoformat(),
        "regions": ["Mumbai"],
        "devices": ["MOBILE"],
        "subscription_tiers": ["PREMIUM"],
    }
    
    # Create campaign in PostgreSQL
    create_response = client.post(
        "/campaigns",
        json=campaign_input,
    )
    
    assert create_response.status_code == 201
    campaign_id = create_response.json()["id"]
    
    # Make campaign eligible by settinng status to ACTIVE
    campaign = db_session.get(Campaign, campaign_id)
    campaign.status = "ACTIVE"
    db_session.commit()
    
    # Initialize its runtime capacity in Redis
    allocation_store = AllocationStore(redis_client)

    capacity_key = allocation_store._remaining_capacity_key(campaign_id)
    
    # Prevent stale Redis state from a previous test run
    redis_client.delete(capacity_key)

    initialized = allocation_store.initialize_new_campaign_capacity(
        campaign_id=campaign_id,
        capacity=campaign.capacity,
    )

    assert initialized is True

    # Unique values prevent idempotency/frequency state from another run
    request_id = f"decision-test-{uuid4()}"
    viewer_id = f"viewer-test-{uuid4()}"
    
    # Ask Rush to actually make a decision
    response = client.post(
        "/decisions",
        json={
            "request_id": request_id,
            "viewer_id": viewer_id,
            "region": "Mumbai",
            "device": "MOBILE",
            "subscription_tier": "PREMIUM",
        },
    )
    
    # Rush should allocate this campaign
    assert response.status_code == 200

    body = response.json()

    assert body["request_id"] == request_id
    assert body["status"] == "ALLOCATED"
    assert body["campaign_id"] == campaign_id

    # Allocation should have consumed exactly one unit
    assert allocation_store.get_remaining_capacity(campaign_id) == 99
    
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
        },
    )

    assert response.status_code == 200

    body = response.json()

    assert body["request_id"] == request_id
    assert body["status"] == "NO_FILL"
    assert body["campaign_id"] is None
    
    
def test_no_fill_is_not_sticky_and_can_succeed_on_retry(db_session):
    now = datetime.now(timezone.utc)

    request_id = f"retry-no-fill-{uuid4()}"
    viewer_id = f"viewer-retry-{uuid4()}"

    decision_request = {
        "request_id": request_id,
        "viewer_id": viewer_id,
        "region": "Mumbai",
        "device": "MOBILE",
        "subscription_tier": "PREMIUM",
    }

    # First attempt: no campaign exists yet
    first_response = client.post(
        "/decisions",
        json=decision_request,
    )

    assert first_response.status_code == 200

    first_body = first_response.json()

    assert first_body["status"] == "NO_FILL"
    assert first_body["campaign_id"] is None

    # A valid campaign becomes available afterward
    campaign_input = {
        "name": "Retry Campaign",
        "capacity": 100,
        "frequency_cap_per_hour": 3,
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

    allocation_store = AllocationStore(redis_client)

    allocation_store.initialize_new_campaign_capacity(
        campaign_id=campaign_id,
        capacity=campaign.capacity,
    )

    # Retry the SAME request_id
    second_response = client.post(
        "/decisions",
        json=decision_request,
    )

    assert second_response.status_code == 200

    second_body = second_response.json()

    assert second_body["request_id"] == request_id
    assert second_body["status"] == "ALLOCATED"
    assert second_body["campaign_id"] == campaign_id
    
def test_decision_returns_no_fill_when_redis_fails():
    request_id = f"redis-failure-{uuid4()}"
    viewer_id = f"viewer-redis-failure-{uuid4()}"

    with patch(
        "app.services.decision_service.AllocationStore.get_existing_decision",
        side_effect=RedisError("Redis unavailable"),
    ):
        response = client.post(
            "/decisions",
            json={
                "request_id": request_id,
                "viewer_id": viewer_id,
                "region": "Mumbai",
                "device": "MOBILE",
                "subscription_tier": "PREMIUM",
            },
        )

    assert response.status_code == 200

    body = response.json()

    assert body["request_id"] == request_id
    assert body["status"] == "NO_FILL"
    assert body["campaign_id"] is None
    
def test_duplicate_decision_request_does_not_consume_twice():
    now = datetime.now(timezone.utc)

    campaign_input = {
        "name": "Duplicate Request Campaign",
        "capacity": 100,
        "frequency_cap_per_hour": 3,
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

    allocation_store = AllocationStore(redis_client)
    capacity_key = allocation_store._remaining_capacity_key(campaign_id)

    # Prevent stale Redis state
    redis_client.delete(capacity_key)

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
    }

    # First request performs the real allocation
    first_response = client.post(
        "/decisions",
        json=decision_request,
    )

    assert first_response.status_code == 200
    assert first_response.json()["status"] == "ALLOCATED"
    assert first_response.json()["campaign_id"] == campaign_id

    # Same logical request is retried
    second_response = client.post(
        "/decisions",
        json=decision_request,
    )

    assert second_response.status_code == 200
    assert second_response.json()["status"] == "ALLOCATED"
    assert second_response.json()["campaign_id"] == campaign_id

    # Capacity must have been consumed only once
    assert allocation_store.get_remaining_capacity(campaign_id) == 99
    
    
    
# candidate A → CAPACITY_EXHAUSTED
#              ↓ keep going
# candidate B → ALLOCATED
#              ↓
#              winner
def test_decision_skips_exhausted_candidate_and_allocates_next(db_session):
    now = datetime.now(timezone.utc)

    campaign_input = {
        "capacity": 100,
        "frequency_cap_per_hour": 3,
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
            "name": "Exhausted Campaign",
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

    allocation_store = AllocationStore(redis_client)

    redis_client.delete(
        allocation_store._remaining_capacity_key(first_id),
        allocation_store._remaining_capacity_key(second_id),
    )

    client.post(f"/campaigns/{first_id}/activate")
    client.post(f"/campaigns/{second_id}/activate")

    # First candidate has no remaining capacity
    redis_client.set(
        allocation_store._remaining_capacity_key(first_id),
        0,
    )

    first_campaign = db_session.get(Campaign, first_id)
    second_campaign = db_session.get(Campaign, second_id)

    request_id = f"fallback-test-{uuid4()}"

    # Force deterministic candidate order:
    # exhausted campaign first, healthy campaign second.
    with patch.object(
        CampaignRepository,
        "find_candidates",
        return_value=[first_campaign, second_campaign],
    ):
        response = client.post(
            "/decisions",
            json={
                "request_id": request_id,
                "viewer_id": f"viewer-{uuid4()}",
                "region": "Mumbai",
                "device": "MOBILE",
                "subscription_tier": "PREMIUM",
            },
        )

    assert response.status_code == 200

    body = response.json()

    assert body["status"] == "ALLOCATED"
    assert body["campaign_id"] == second_id

    assert allocation_store.get_remaining_capacity(first_id) == 0
    assert allocation_store.get_remaining_capacity(second_id) == 99