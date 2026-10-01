from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.models.campaign import Campaign
from app.main import app
import pytest
from datetime import datetime, timezone


# TestClient lets us call the FastAPI app directly without running Uvicorn.
client = TestClient(app)


def test_create_and_get_campaign():
    # Create a campaign
    test_input_campaign = {
        "name": "Nike",
        "capacity": 62,
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
        "start_time": "2026-10-10T18:00:00",
        "end_time": "2026-10-25T23:00:00",
    }

    test_B_input_campaign = {
        "name": "Swiggy",
        "capacity": 100,
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

