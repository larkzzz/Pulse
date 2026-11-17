import importlib

import pytest
from fastapi.testclient import TestClient

from app import mock_data
from app.main import app


@pytest.fixture(autouse=True)
def fresh_data():
    """Каждый тест начинает с исходных тестовых данных."""
    importlib.reload(mock_data)
    yield


@pytest.fixture
def client():
    return TestClient(app)
