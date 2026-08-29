from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health() -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_system_requires_admin_token() -> None:
    assert client.get("/api/v1/system").status_code == 401


def test_sending_is_disabled_by_default() -> None:
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["external_send_enabled"] is False

