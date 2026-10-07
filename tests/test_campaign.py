from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.models.campaign import Campaign
from app.main import app
import pytest
from datetime import datetime, timezone, timedelta

from app.runtime.allocation_store import AllocationStore
from app.runtime.redis_client import redis_client

# TestClient lets us call the FastAPI app directly without running Uvicorn.
client = TestClient(app)


def test_create_and_get_campaign():
    # Create a campaign
    test_input_campaign = {
        "name": "Nike",
        "capacity": 62,
        "frequency_cap_per_hour": 3,
        "start_time": "2026-10-10T18:00:00",
        "end_time": "2026-10-25T23:00:00",
    }

    create_response = client.post(
        "/campaigns",
        json=test_input_campaign,
    )

    assert create_response.status_code == 201

    create_response_body = create_response.json()

    # Use whatever ID PostgreSQL generated
    campaign_id = create_response_body["id"]

    # Fetch the same campaign using that ID
    get_response = client.get(
        f"/campaigns/{campaign_id}"
    )

    assert get_response.status_code == 200

    get_response_body = get_response.json()

    # Confirm we got back the same campaign
    assert get_response_body["id"] == campaign_id
    assert get_response_body["name"] == "Nike"
    assert get_response_body["capacity"] == 62
    assert get_response_body["status"] == "DRAFT"

def test_get_all_campaigns():
    # Test starts with an empty campaigns table
    # because clean_campaigns() in conftest.py runs automatically.

    test_A_input_campaign = {
        "name": "Nike",
        "capacity": 62,
        "frequency_cap_per_hour": 3,
        "start_time": "2026-10-10T18:00:00",
        "end_time": "2026-10-25T23:00:00",
    }

    test_B_input_campaign = {
        "name": "Swiggy",
        "capacity": 100,
        "frequency_cap_per_hour": 3,
        "start_time": "2026-11-10T18:00:00",
        "end_time": "2026-12-25T23:00:00",
    }

    # Create both campaigns
    client.post("/campaigns", json=test_A_input_campaign)
    client.post("/campaigns", json=test_B_input_campaign)

    # Ask Rush for all campaigns
    response = client.get("/campaigns")

    # GET /campaigns should succeed
    assert response.status_code == 200

    # Convert the JSON response into a Python object
    response_body = response.json()

    # GET /campaigns should return a list
    assert isinstance(response_body, list)

    # We created exactly two campaigns
    assert len(response_body) == 2

    # Extract just the campaign names from the response
    names = [campaign["name"] for campaign in response_body]

    # Confirm both campaigns were returned
    assert "Nike" in names
    assert "Swiggy" in names
    
def test_database_rejects_invalid_capacity(db_session):
    invalid_campaign = Campaign(
        name="Invalid Campaign",
        capacity=-10,
        frequency_cap_per_hour=3,
        start_time="2026-10-10T18:00:00+00:00",
        end_time="2026-10-10T20:00:00+00:00",
        status="DRAFT",
    )

    db_session.add(invalid_campaign)

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

def test_database_rejects_invalid_time_window(db_session):
    # This request should fail because start time should be < end time
        invalid_time_window_campaign = Campaign(
        name="Swiggy",
        capacity=50,
        frequency_cap_per_hour=3,
        start_time=datetime(
            2026, 11, 10, 18, 0,
            tzinfo=timezone.utc,
        ),
        end_time=datetime(
            2026, 10, 25, 23, 0,
            tzinfo=timezone.utc,
        ),
        status="DRAFT",
    )
    
        
        db_session.add(invalid_time_window_campaign)
        
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()
    
        

def test_get_nonexistent_campaign():
    # This request should fail because campaign does not exist
    test_campaign_id = 6

    #The campaign ID is passed as a path parameter in the URL.
    response = client.get(f"/campaigns/{test_campaign_id}")
    
    # The router translates "campaign not found" into HTTP 404.
    assert response.status_code == 404
    
    
def test_activate_campaign_initializes_redis_capacity():
    now = datetime.now(timezone.utc)

    campaign_input = {
        "name": "Activation Test Campaign",
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

    redis_client.delete(capacity_key)

    activate_response = client.post(
        f"/campaigns/{campaign_id}/activate"
    )

    assert activate_response.status_code == 200

    body = activate_response.json()

    assert body["status"] == "ACTIVE"
    assert allocation_store.get_remaining_capacity(campaign_id) == 100
    

def test_reactivating_campaign_does_not_reset_redis_capacity():
    now = datetime.now(timezone.utc)

    campaign_input = {
        "name": "Reactivation Test Campaign",
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

    campaign_id = create_response.json()["id"]

    allocation_store = AllocationStore(redis_client)
    capacity_key = allocation_store._remaining_capacity_key(campaign_id)

    redis_client.delete(capacity_key)

    # First activation initializes capacity to 100
    first_activation = client.post(
        f"/campaigns/{campaign_id}/activate"
    )

    assert first_activation.status_code == 200
    assert allocation_store.get_remaining_capacity(campaign_id) == 100

    # Simulate one unit already being consumed
    redis_client.decr(capacity_key)

    assert allocation_store.get_remaining_capacity(campaign_id) == 99

    # Activate the same campaign again
    second_activation = client.post(
        f"/campaigns/{campaign_id}/activate"
    )

    assert second_activation.status_code == 200

    # NX must prevent Redis capacity from being reset to 100
    assert allocation_store.get_remaining_capacity(campaign_id) == 99
    
    


