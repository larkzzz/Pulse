from dataclasses import replace
from datetime import datetime, timedelta
from hashlib import sha256
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import Fap, User
from app.routers import auth, help
from app.services.demo import DEMO_PASSWORD
from app.services.invites import limited_code_status


@pytest.mark.parametrize("url", [
    "/login", "/register", "/register?code=PLS-4827", "/register/no-code", "/pending",
])
def test_public_pages_open(client, url):
    assert client.get(url).status_code == 200


def test_admin_page_uses_versioned_stylesheet(admin_client):
    page = admin_client.get("/admin/invites")
    css = admin_client.get("/static/css/pulse.css")
    version = sha256(css.content).hexdigest()[:12]
    assert page.status_code == css.status_code == 200
    assert f"/static/css/pulse.css?v={version}" in page.text
    assert ".admin-layout" in css.text


@pytest.mark.parametrize("url", ["/", "/cabinet"])
def test_patient_pages_require_login(client, url):
    response = client.get(url, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/login"


@pytest.mark.parametrize("url", ["/", "/cabinet"])
def test_patient_pages_open(patient_client, url):
    assert patient_client.get(url).status_code == 200


def test_help_opens_with_emergency_and_registry_calls(patient_client, fap_directory, monkeypatch):
    monkeypatch.setattr(help, "settings", replace(help.settings, registry_phone="+7 (84367) 3-12-34"))
    response = patient_client.get("/help")
    assert response.status_code == 200
    assert 'href="tel:112"' in response.text
    assert 'href="tel:+78436731234"' in response.text
    assert "Регистратура поможет связаться с вашим врачом." in response.text
    assert 'href="/cabinet"' in response.text
    assert "Найти медпункт рядом" in response.text
    locate_tag = re.search(r'<button[^>]+id="help-locate"[^>]*>', response.text)
    assert locate_tag and "hidden" not in locate_tag.group(0)
    assert "Мы его не сохраняем" in response.text
    assert "Выбрать медпункт вручную" in response.text


def test_help_manual_choice_can_open_route_without_maps_key(patient_client, fap_directory, monkeypatch):
    monkeypatch.setattr(help, "settings", replace(help.settings, registry_phone="", yandex_maps_api_key=""))
    response = patient_client.get("/help", params={"fap_id": fap_directory[0].id})
    assert "ул. Тестовая, 1" in response.text
    assert 'href="tel:+78436731234"' in response.text
    assert "rtext=~55.76%2C49.66" in response.text
    assert "В Яндекс Картах укажите, откуда поедете." in response.text
    assert "Номер регистратуры пока не указан" in response.text
    assert "Показать карту" not in response.text
    assert "Не нашли этот медпункт" in patient_client.get("/help?fap_id=999").text


@pytest.mark.parametrize("point", [(None, None), (0, 0)])
def test_help_manual_choice_falls_back_to_address_if_coordinates_unusable(patient_client, point):
    latitude, longitude = point
    fap = Fap(name="Медпункт без координат", village="Пановка", address="ул. Тестовая, 1",
              phone="8 800 000-00-01", latitude=latitude, longitude=longitude)
    with SessionLocal.begin() as db:
        db.add(fap)
    response = patient_client.get("/help", params={"fap_id": fap.id})
    assert "Найти адрес в Яндекс Картах" in response.text
    assert "Найти медпункт рядом" not in response.text
    assert "Показать карту" not in response.text
    assert "tel:88000000001" not in response.text


def test_help_nearest_handles_good_and_bad_coordinates(patient_client, fap_directory, monkeypatch):
    monkeypatch.setattr(help, "settings", replace(help.settings, yandex_maps_api_key="test-key"))
    response = patient_client.post("/help/nearest", data={"latitude": "55.70", "longitude": "49.71"})
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert "Ближайший по прямой" in response.text and "ул. Тестовая, 2" in response.text
    assert "ул. Тестовая, 1" not in response.text
    assert "49.71" not in response.text
    assert 'data-latitude="55.69" data-longitude="49.73"' in response.text
    assert "data-fap-route" in response.text
    assert "Показать карту" in response.text
    for invalid in ("не число", "nan", "91", ""):
        response = patient_client.post("/help/nearest", data={"latitude": invalid, "longitude": "49"})
        assert "Выберите медпункт из списка" in response.text
        assert "Ближайший по прямой" not in response.text
    zero_point = patient_client.post("/help/nearest", data={"latitude": "0", "longitude": "0"})
    assert "Не удалось определить ваше местоположение" in zero_point.text


def test_help_loads_versioned_map_script_when_configured(patient_client, fap_directory, monkeypatch):
    monkeypatch.setattr(help, "settings", replace(help.settings, yandex_maps_api_key="test-key"))
    page = patient_client.get("/help", params={"fap_id": fap_directory[0].id})
    script = patient_client.get("/static/js/help-map.js")
    assert page.status_code == script.status_code == 200
    assert "api-maps.yandex.ru/v3/" in script.text
    assert f"/static/js/help-map.js?v={sha256(script.content).hexdigest()[:12]}" in page.text
    assert "Показать карту" in page.text


def test_help_remains_available_with_empty_directory(patient_client):
    response = patient_client.get("/help")
    assert 'href="tel:112"' in response.text
    assert "Медпункты пока не добавлены" in response.text
    assert "Найти медпункт рядом" not in response.text
    assert "Не удалось найти ближайший медпункт" in patient_client.post(
        "/help/nearest", data={"latitude": "55.75", "longitude": "49.65"}
    ).text


@pytest.mark.parametrize("village,address,latitude,longitude", [
    ("пестречи", "ул. Садовая, д. 18", 55.76, 49.66),
    ("Другой посёлок", "ул. Исправленная, 1", 55.75, 49.65),
])
def test_help_hides_fap_with_partial_demo_data(patient_client, village, address, latitude, longitude):
    fap = Fap(name="Переименованный ФАП", village=village, address=address, phone="+7 (84367) 3-12-34",
              latitude=latitude, longitude=longitude)
    with SessionLocal.begin() as db:
        db.add(fap)
    page = patient_client.get("/help", params={"fap_id": fap.id})
    assert "Медпункты пока не добавлены" in page.text
    assert fap.name not in page.text and fap.address not in page.text
    nearest = patient_client.post("/help/nearest", data={"latitude": "55.75", "longitude": "49.65"})
    assert "Не удалось найти ближайший медпункт" in nearest.text
    assert fap.name not in nearest.text
    with SessionLocal.begin() as db:
        assert db.get(Fap, fap.id).address == fap.address


@pytest.mark.parametrize("status", ["pending", "rejected"])
def test_help_for_unconfirmed_patient_does_not_grant_cabinet_access(client, fap_directory, status):
    with SessionLocal.begin() as db:
        user = db.scalar(select(User).where(User.email == "petrov@mail.ru"))
        user.status = status
    client.post("/login", data={"email": "petrov@mail.ru", "password": DEMO_PASSWORD})
    assert 'href="/help"' in client.get("/pending").text
    response = client.get("/help", params={"fap_id": fap_directory[0].id})
    assert response.status_code == 200
    assert 'href="tel:112"' in response.text and "ул. Тестовая, 1" in response.text
    assert 'href="/pending"' in response.text and 'href="/cabinet"' not in response.text
    assert "Регистратура поможет связаться с вашим врачом." not in response.text
    assert client.post("/help/nearest", data={"latitude": "55.75", "longitude": "49.65"}).status_code == 200
    assert client.get("/cabinet", follow_redirects=False).status_code == 303


def test_help_requires_a_patient_session(client):
    assert client.get("/help", follow_redirects=False).headers["location"] == "/login"
    assert client.post("/help/nearest", follow_redirects=False).headers["location"] == "/login"
    client.post("/login", data={"email": "admin@pestrechi-crb.ru", "password": DEMO_PASSWORD})
    assert client.get("/help").status_code == 403
    assert client.post("/help/nearest").status_code == 403


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


def test_pending_keeps_emergency_contacts_without_broken_help_link(client, monkeypatch):
    monkeypatch.setattr(auth, "settings", replace(auth.settings, registry_phone="+7 (84367) 3-12-34"))
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
