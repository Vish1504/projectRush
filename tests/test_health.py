from fastapi.testclient import TestClient

from app.main import app

# Create something that can make test requests against Rush.
client = TestClient(app)


def test_health():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}