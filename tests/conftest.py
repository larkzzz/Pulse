import os

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import pytest
from fastapi.testclient import TestClient

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import Fap
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


@pytest.fixture
def fap_directory(client):
    faps = (
        Fap(name="ФАП Северный", village="Северный", address="ул. Тестовая, 1",
            phone="+7 (84367) 3-12-34", latitude=55.76, longitude=49.66),
        Fap(name="ФАП Южный", village="Южный", address="ул. Тестовая, 2",
            latitude=55.69, longitude=49.73),
    )
    with SessionLocal.begin() as db:
        db.add_all(faps)
    return faps
