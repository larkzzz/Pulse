import os

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import pytest
from fastapi.testclient import TestClient

from app.database import Base, SessionLocal, engine
from app.main import app
from app.services.demo import DEMO_PASSWORD, seed_demo_data


@pytest.fixture
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as test_client:
        with SessionLocal() as db:
            seed_demo_data(db)
        yield test_client


@pytest.fixture
def patient_client(client):
    response = client.post(
        "/login", data={"email": "galina.smirnova@mail.ru", "password": DEMO_PASSWORD}
    )
    assert response.headers["HX-Redirect"] == "/"
    return client


@pytest.fixture
def admin_client(client):
    response = client.post(
        "/login", data={"email": "admin@pestrechi-crb.ru", "password": DEMO_PASSWORD}
    )
    assert response.headers["HX-Redirect"] == "/admin"
    return client
