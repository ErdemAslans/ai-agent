"""Tests for the user registration API."""
import pytest

from app import app, _USERS


@pytest.fixture(autouse=True)
def reset_users():
    """Clear the in-memory user store before each test."""
    _USERS.clear()
    yield
    _USERS.clear()


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_register_success(client):
    response = client.post(
        "/users/register",
        json={"email": "alice@example.com", "password": "secret123"},
    )
    assert response.status_code == 201
    body = response.get_json()
    assert body["email"] == "alice@example.com"


def test_register_missing_email(client):
    response = client.post("/users/register", json={"password": "secret123"})
    assert response.status_code == 400


def test_register_missing_password(client):
    response = client.post("/users/register", json={"email": "alice@example.com"})
    assert response.status_code == 400


def test_list_users_initially_empty(client):
    response = client.get("/users")
    assert response.status_code == 200
    assert response.get_json() == []


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"
