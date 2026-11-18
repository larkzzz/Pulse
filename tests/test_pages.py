from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import auth
from app.services.demo import DEMO_PASSWORD
from app.services.invites import limited_code_status


@pytest.mark.parametrize("url", [
    "/login", "/register", "/register?code=PLS-4827", "/register/no-code", "/pending",
])
def test_public_pages_open(client, url):
    assert client.get(url).status_code == 200


@pytest.mark.parametrize("url", ["/", "/cabinet"])
def test_patient_pages_require_login(client, url):
    response = client.get(url, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/login"


@pytest.mark.parametrize("url", ["/", "/cabinet"])
def test_patient_pages_open(patient_client, url):
    assert patient_client.get(url).status_code == 200


def test_take_intake_marks_next(patient_client):
    response = patient_client.post("/intakes/2/1300/take")
    assert response.status_code == 200 and "Метформин принят" in response.text
    assert "Принято 2 из 4" in response.text


def test_take_intake_rejects_invalid_time(patient_client):
    assert patient_client.post("/intakes/2/9999/take").status_code == 404


def test_add_medication_requires_fields(patient_client):
    response = patient_client.post("/cabinet/medications", data={})
    assert "Напишите название лекарства" in response.text


def test_add_and_delete_medication(patient_client):
    response = patient_client.post(
        "/cabinet/medications",
        data={"drug": "Амлодипин", "dose": "5 мг", "time1": "09:30", "until": "2027-12-31"},
    )
    assert "Амлодипин добавлен" in response.text
    assert "до 31 декабря" in response.text
    response = patient_client.delete("/cabinet/medications/4")
    assert response.status_code == 200
    assert '<p class="med__drug">Амлодипин</p>' not in response.text


def test_login_checks_password_and_redirects_by_status(client):
    response = client.post(
        "/login", data={"email": "galina.smirnova@mail.ru", "password": "wrong-password"}
    )
    assert "Неверный пароль" in response.text
    response = client.post(
        "/login", data={"email": "galina.smirnova@mail.ru", "password": DEMO_PASSWORD}
    )
    assert response.headers["HX-Redirect"] == "/"
    client.get("/logout")
    response = client.post("/login", data={"email": "petrov@mail.ru", "password": DEMO_PASSWORD})
    assert response.headers["HX-Redirect"] == "/pending"


def test_logout_clears_session(patient_client):
    patient_client.get("/logout")
    assert patient_client.get("/", follow_redirects=False).status_code == 303


def _profile(**extra):
    return {"last_name": "Иванов", "first_name": "Пётр", "birth_date": "1948-05-02",
            "phone": "+7 917 111-22-33", "email": "ivanov@mail.ru",
            "password": "secret123", "password2": "secret123", "consent": "1", **extra}


def test_register_by_code_assigns_doctor(client):
    response = client.post("/register", data=_profile(mode="code", code="pls 3159"))
    assert response.headers["HX-Redirect"] == "/cabinet?welcome=1"
    cabinet = client.get("/cabinet?welcome=1")
    assert "Гарипова Алсу Маратовна" in cabinet.text
    assert "Лекарств пока нет" in cabinet.text
    response = client.post("/register", data=_profile(mode="code", code="PLS-3159", email="other@mail.ru"))
    assert "уже зарегистрировались" in response.text


def test_invite_block_applies_to_all_code_checks_but_not_no_code_request(client):
    bad, good = "PLS-9999", "PLS-4827"
    message = "Слишком много неверных попыток"
    for _ in range(2):
        assert "Код не найден" in client.get("/register", params={"code": bad}).text
        assert "Код не найден" in client.post("/register/check-code", data={"code": bad}).text
    assert message in client.post("/register", data=_profile(code=bad)).text
    for response in (
        client.get("/register", params={"code": good}),
        client.post("/register/check-code", data={"code": good}),
        client.post("/register", data=_profile(code=good)),
    ):
        assert message in response.text
        assert "Ваш врач:" not in response.text
        assert "HX-Redirect" not in response.headers

    response = client.post("/register", data=_profile(mode="request", oms="1655 1111 2222 3333"))
    assert response.headers["HX-Redirect"] == "/pending"


def test_invite_block_is_shared_by_ip_and_stays_with_session(client):
    for _ in range(5):
        client.post("/register/check-code", data={"code": "PLS-9999"})
    message = "Слишком много неверных попыток"
    with_cookies = dict(client.cookies)
    with TestClient(app, client=("192.0.2.3", 50000), cookies=with_cookies) as other_address:
        assert message in other_address.get("/register", params={"code": "PLS-4827"}).text
    client.cookies.clear()
    assert message in client.get("/register", params={"code": "PLS-4827"}).text


def test_invite_block_expires_without_extending_on_blocked_requests(client, monkeypatch):
    now = datetime(2026, 11, 18, 12)

    def check(db, raw, keys):
        return limited_code_status(db, raw, keys, now=now)

    monkeypatch.setattr(auth, "limited_code_status", check)
    for _ in range(5):
        client.post("/register/check-code", data={"code": "PLS-9999"})

    now += timedelta(minutes=14)
    assert "Слишком много неверных попыток" in client.get("/register?code=PLS-4827").text
    now += timedelta(minutes=1)
    response = client.post("/register", data=_profile(code="PLS-4827"))
    assert response.headers["HX-Redirect"] == "/cabinet?welcome=1"


def test_invite_valid_check_resets_streak_and_blank_does_not_count(client):
    for _ in range(5):
        assert "Слишком много неверных попыток" not in client.post("/register/check-code").text
    for _ in range(2):
        for _ in range(4):
            assert "Код не найден" in client.post("/register/check-code", data={"code": "PLS-9999"}).text
        assert "Ваш врач:" in client.post("/register/check-code", data={"code": "PLS-4827"}).text


def test_register_request_without_code(client):
    response = client.post("/register", data=_profile(mode="request", oms="123"))
    assert "16 цифр" in response.text
    response = client.post("/register", data=_profile(mode="request", oms="1655 1111 2222 3333"))
    assert response.headers["HX-Redirect"] == "/pending"
    assert client.get("/", follow_redirects=False).status_code == 303


def test_pending_keeps_emergency_contacts_without_broken_help_link(client):
    response = client.get("/pending")
    assert 'href="tel:112"' in response.text
    assert "Позвонить в регистратуру" in response.text
    assert "Адрес ближайшего ФАП можно узнать в регистратуре" in response.text
    assert 'href="/help"' not in response.text


def test_profile_email_is_used_for_login(patient_client):
    response = patient_client.post("/cabinet/profile", data={
        "last_name": "Смирнова", "first_name": "Галина", "patronymic": "Петровна",
        "phone": "+7 917 555-12-34", "email": "new.galina@mail.ru",
    })
    assert "Сохранено" in response.text
    patient_client.get("/logout")
    response = patient_client.post(
        "/login", data={"email": "new.galina@mail.ru", "password": DEMO_PASSWORD}
    )
    assert response.headers["HX-Redirect"] == "/"


def test_patient_cannot_delete_another_patients_medication(client):
    client.post("/register", data=_profile(mode="code", code="PLS-4827"))
    assert client.delete("/cabinet/medications/1").status_code == 404


def test_staff_role_cannot_open_patient_pages(client):
    response = client.post(
        "/login", data={"email": "safin@pestrechi-crb.ru", "password": DEMO_PASSWORD}
    )
    assert response.headers["HX-Redirect"] == "/doctor"
    assert client.get("/").status_code == 403
