from fastapi.testclient import TestClient

from app.main import app


# TestClient lets us call the FastAPI app directly without running Uvicorn.
client = TestClient(app)


def test_create_campaign():
    # Simulate the JSON body a real client would send to POST /campaigns.
    test_input_campaign = {
        "name": "Nike",
        "capacity": 62,
        "start_time": "2026-10-10T18:00:00",
        "end_time": "2026-10-25T23:00:00",
    }

    response = client.post("/campaigns", json=test_input_campaign)

    # response.json() gives us the JSON response body as a Python dictionary.
    response_body = response.json()

    # Successful creation should return HTTP 201 Created.
    assert response.status_code == 201

    # Rush, not the client, assigns the initial campaign status.
    assert response_body["status"] == "DRAFT"

    # Confirm that client-provided fields are returned correctly.
    assert response_body["name"] == "Nike"
    assert response_body["capacity"] == 62


def test_create_campaign_with_invalid_capacity():
    # This request should fail because CampaignCreate requires capacity > 0.
    test_input_campaign = {
        "name": "Swiggy",
        "capacity": -5,
        "start_time": "2026-10-10T18:00:00",
        "end_time": "2026-10-25T23:00:00",
    }

    response = client.post("/campaigns", json=test_input_campaign)

    # Pydantic rejects the request before the route/service logic runs.
    assert response.status_code == 422
    # assert response_body["status"] == "DRAFT"
    # assert response_body["name"] == "Nike"
    # assert response_body["capacity"] == -5
    # assert response_body["status"] == 200
