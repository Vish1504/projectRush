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

    decision_response = client.post(
        "/decisions/candidates",
        json={
            "request_id": "test-request-1",
            "viewer_id": "test-viewer-1",
            "region": "Mumbai",
            "device": "MOBILE",
            "subscription_tier": "PREMIUM",
            },
    )

    assert decision_response.status_code == 200

    candidates = decision_response.json()

    assert len(candidates) == 1
    assert candidates[0]["id"] == campaign_id
    
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

    response = client.post(
        "/decisions/candidates",
        json=decision_request,
    )

    assert response.status_code == 200
    assert response.json() == []
    
def test_draft_campaign_is_rejected():
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

    response = client.post("/campaigns", json=campaign_input)
    assert response.status_code == 201

    # We deliberately do NOT change DRAFT -> ACTIVE.

    decision_response = client.post(
        "/decisions/candidates",
        json={
            "request_id": "test-request-1",
            "viewer_id": "test-viewer-1",
            "region": "Mumbai",
            "device": "MOBILE",
            "subscription_tier": "PREMIUM",
        },
    )

    assert decision_response.status_code == 200
    assert decision_response.json() == []
    
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

    response = client.post(
        "/decisions/candidates",
        json={
            "request_id": "test-request-1",
            "viewer_id": "test-viewer-1",
            "region": "Mumbai",
            "device": "MOBILE",
            "subscription_tier": "PREMIUM",
        },
    )

    assert response.status_code == 200
    assert response.json() == []
    
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

    response = client.post(
        "/decisions/candidates",
        json={
            "request_id": "unrestricted-request-1",
            "viewer_id": "test-viewer-1",
            "region": "Bengaluru",
            "device": "SOME_RANDOM_DEVICE",
            "subscription_tier": "FREE",
        },
    )

    assert response.status_code == 200

    candidates = response.json()

    assert len(candidates) == 1
    assert candidates[0]["id"] == campaign_id
    

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

    response = client.post(
        "/decisions/candidates",
        json={
            "request_id": "eligible-request-1",
            "viewer_id": "test-viewer-1",
            "region": "Mumbai",
            "device": "MOBILE",
            "subscription_tier": "PREMIUM",
        },
    )

    returned_ids = {
        campaign["id"]
        for campaign in response.json()
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