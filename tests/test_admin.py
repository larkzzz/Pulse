import re
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from app.database import SessionLocal, migrate_schema
from app.models import Doctor, Drug, Fap, InviteCode, RegistrationRequest, User
from app.services import admin as admin_service
from app.services.demo import DEMO_PASSWORD
from app.services.invites import transition_invite

ADMIN_URLS = [
    "/admin/requests", "/admin/invites", "/admin/patients", "/admin/doctors",
    "/admin/references/faps", "/admin/references/drugs",
]
ADMIN_ACTIONS = [
    "/admin/invites", "/admin/invites/1/revoke", "/admin/requests/1/review",
    "/admin/patients/3/doctor", "/admin/doctors", "/admin/doctors/1/status",
    "/admin/references/faps", "/admin/references/faps/1", "/admin/references/faps/1/delete",
    "/admin/references/drugs", "/admin/references/drugs/1", "/admin/references/drugs/1/delete",
]


@pytest.mark.parametrize(("email", "expected_status"), [
    (None, 303),
    ("galina.smirnova@mail.ru", 403),
    ("safin@pestrechi-crb.ru", 403),
])
def test_admin_pages_and_actions_require_admin(client, email, expected_status):
    if email:
        response = client.post("/login", data={"email": email, "password": DEMO_PASSWORD})
        assert response.headers["HX-Redirect"] in ("/", "/doctor")
    for method, urls in (("GET", ["/admin", *ADMIN_URLS]), ("POST", ADMIN_ACTIONS)):
        for url in urls:
            response = client.request(method, url, follow_redirects=False)
            assert response.status_code == expected_status, (method, url)
            if expected_status == 303:
                assert response.headers["location"] == "/login"


def test_admin_pages_open(admin_client):
    assert admin_client.get("/admin").url.path == "/admin/requests"
    for url in ADMIN_URLS:
        response = admin_client.get(url)
        assert response.status_code == 200
        assert "Кабинет администратора" in response.text
        assert 'aria-current=' in response.text


def test_invite_create_and_revoke(admin_client):
    response = admin_client.post("/admin/invites", data={"doctor_id": "1", "patient_hint": ""})
    assert "Напишите фамилию" in response.text

    response = admin_client.post(
        "/admin/invites", data={"doctor_id": "1", "patient_hint": "Кузнецов А. В."}
    )
    match = re.search(r"Создан код (PLS-\d{4})", response.text)
    assert match and "Кузнецов А. В." in response.text
    with SessionLocal() as db:
        invite = db.query(InviteCode).filter_by(code=match.group(1)).one()
        assert invite.expires_at.toordinal() - invite.issued_at.toordinal() == 30
        invite_id = invite.id

    response = admin_client.post(f"/admin/invites/{invite_id}/revoke")
    assert f"Код {match.group(1)} отменён" in response.text
    with SessionLocal() as db:
        assert db.get(InviteCode, invite_id).status == "revoked"
    for non_current_id in (invite_id, 3, 4):
        response = admin_client.post(f"/admin/invites/{non_current_id}/revoke")
        assert "Можно отменить только действующий код" in response.text
    response = admin_client.get(f"/register?code={match.group(1)}")
    assert "Срок действия кода закончился" in response.text


def test_invite_list_shows_expiration_without_modifying_code(admin_client):
    with SessionLocal() as db:
        invite = db.get(InviteCode, 1)
        invite.expires_at = date.today() - timedelta(days=1)
        db.commit()
    response = admin_client.get("/admin/invites")
    assert re.search(r"PLS-4827(?:(?!</tr>).)*>Истёк<", response.text, re.S)
    with SessionLocal() as db:
        assert db.get(InviteCode, 1).status == "new"


def test_create_invite_retries_taken_code(admin_client, monkeypatch):
    candidates = iter((4827, 8001, 8001, 8002))
    monkeypatch.setattr(admin_service.secrets, "randbelow", lambda _: next(candidates))
    form = {"doctor_id": "1", "patient_hint": "Тестовый пациент"}
    first = admin_client.post("/admin/invites", data=form)
    second = admin_client.post("/admin/invites", data=form)
    assert "Создан код PLS-8001" in first.text
    assert "Создан код PLS-8002" in second.text
    monkeypatch.setattr(admin_service.secrets, "randbelow", lambda _: 4827)
    assert "Не удалось выдать новый код" in admin_client.post("/admin/invites", data=form).text


def test_admin_invite_registers_one_patient_under_selected_doctor(admin_client):
    response = admin_client.post("/admin/invites", data={
        "doctor_id": "2", "patient_hint": "Кузнецов А. В.",
    })
    match = re.search(r"Создан код (PLS-\d{4})", response.text)
    assert match
    code = match.group(1)
    admin_client.get("/logout")
    form = {
        "code": code, "last_name": "Кузнецов", "first_name": "Андрей", "birth_date": "1955-01-02",
        "phone": "+7 917 111-22-33", "email": "kuznetsov@example.test", "consent": "1",
        "password": "test-pass123", "password2": "test-pass123",
    }
    response = admin_client.post("/register", data=form)
    assert response.headers["HX-Redirect"] == "/cabinet?welcome=1"
    response = admin_client.post("/register", data={**form, "email": "second@example.test"})
    assert "По этому коду уже зарегистрировались" in response.text
    with SessionLocal() as db:
        invite = db.scalar(select(InviteCode).where(InviteCode.code == code))
        assert invite.status == "used"
        assert invite.used_by.email == form["email"] and invite.used_by.doctor_id == 2


def test_revoked_code_cannot_be_used_after_a_stale_read(admin_client):
    with SessionLocal() as stale, SessionLocal() as current:
        invite = stale.get(InviteCode, 1)
        assert invite.status == "new"
        admin_service.revoke_invite(current, invite.id)
        assert not transition_invite(stale, invite.id, "used")
    with SessionLocal() as db:
        invite = db.get(InviteCode, 1)
        assert invite.status == "revoked" and invite.used_by_id is None


def test_approve_request_activates_patient(admin_client):
    response = admin_client.post("/admin/requests/1/review", data={
        "decision": "approve", "doctor_id": "2",
    })
    assert "Заявка подтверждена" in response.text
    with SessionLocal() as db:
        registration = db.get(RegistrationRequest, 1)
        assert registration.status == "approved"
        assert registration.patient.status == "active"
        assert registration.patient.doctor_id == 2
        assert registration.reviewer.role == "admin"

    admin_client.get("/logout")
    response = admin_client.post(
        "/login", data={"email": "petrov@mail.ru", "password": DEMO_PASSWORD}
    )
    assert response.headers["HX-Redirect"] == "/"
    assert admin_client.get("/").status_code == 200
    assert admin_client.get("/pending", follow_redirects=False).headers["location"] == "/"


def test_request_cannot_be_reviewed_again_after_a_stale_read(admin_client):
    with SessionLocal() as stale, SessionLocal() as current:
        request = stale.get(RegistrationRequest, 1)
        assert request.status == "pending"
        admin_id = stale.scalar(select(User.id).where(User.role == "admin"))
        admin_service.review_request(current, request.id, current.get(User, admin_id), "approve", 2)
        with pytest.raises(admin_service.AdminError, match="Эта заявка уже рассмотрена"):
            admin_service.review_request(
                stale, request.id, stale.get(User, admin_id), "reject", rejection_reason="Не совпало",
            )
    with SessionLocal() as db:
        request = db.get(RegistrationRequest, 1)
        assert request.status == "approved"
        assert request.patient.status == "active" and request.patient.doctor_id == 2


def test_reject_request_shows_reason_to_patient(admin_client):
    response = admin_client.post("/admin/requests/1/review", data={"decision": "reject"})
    assert "Напишите причину отказа" in response.text
    response = admin_client.post("/admin/requests/1/review", data={
        "decision": "reject", "rejection_reason": "Данные не совпали с картой пациента",
    })
    assert "Заявка отклонена" in response.text

    admin_client.get("/logout")
    response = admin_client.post(
        "/login", data={"email": "petrov@mail.ru", "password": DEMO_PASSWORD}
    )
    assert response.headers["HX-Redirect"] == "/pending"
    response = admin_client.get("/pending")
    assert "Данные не совпали с картой пациента" in response.text


def test_reassign_patient(admin_client):
    with SessionLocal() as db:
        patient_id = db.query(User).filter_by(email="galina.smirnova@mail.ru").one().id
    response = admin_client.post(f"/admin/patients/{patient_id}/doctor", data={"doctor_id": "2"})
    assert "Смирнова Галина Петровна. Новый врач:" in response.text
    with SessionLocal() as db:
        assert db.get(User, patient_id).doctor_id == 2


def test_assignments_and_invites_reject_unavailable_doctors(admin_client):
    with SessionLocal() as db:
        patient_id = db.scalar(select(User.id).where(User.email == "galina.smirnova@mail.ru"))
    admin_client.post("/admin/doctors/2/status", data={"status": "blocked"})
    for doctor_id in ("2", "999", "²", "9" * 100):
        response = admin_client.post(
            "/admin/invites", data={"doctor_id": doctor_id, "patient_hint": "Тестовый пациент"},
        )
        assert "Выберите врача с действующей учётной записью" in response.text
        response = admin_client.post(
            "/admin/requests/1/review", data={"decision": "approve", "doctor_id": doctor_id},
        )
        assert "Выберите врача, к которому прикрепить пациента" in response.text
        response = admin_client.post(f"/admin/patients/{patient_id}/doctor", data={"doctor_id": doctor_id})
        assert "Выберите врача с действующей учётной записью" in response.text
    with SessionLocal() as db:
        assert db.get(User, patient_id).doctor_id == 1
        assert db.get(RegistrationRequest, 1).status == "pending"


def test_create_and_block_doctor(admin_client):
    response = admin_client.post("/admin/doctors", data={
        "last_name": "Валеева", "first_name": "Лилия", "patronymic": "Рафисовна",
        "specialty": "врач-терапевт", "email": "valeeva@pestrechi-crb.ru",
        "password": "doctor123",
    })
    assert "Учётная запись врача Валеева Лилия Рафисовна создана" in response.text
    with SessionLocal() as db:
        doctor = db.query(Doctor).filter(Doctor.name == "Валеева Лилия Рафисовна").one()
        assert doctor.account.role == "doctor"
        doctor_id = doctor.id

    response = admin_client.post(f"/admin/doctors/{doctor_id}/status", data={"status": "blocked"})
    assert "заблокирована" in response.text
    admin_client.get("/logout")
    response = admin_client.post("/login", data={
        "email": "valeeva@pestrechi-crb.ru", "password": "doctor123",
    })
    assert "Учётная запись заблокирована" in response.text
    admin_client.post("/login", data={"email": "admin@pestrechi-crb.ru", "password": DEMO_PASSWORD})
    response = admin_client.post(f"/admin/doctors/{doctor_id}/status", data={"status": "active"})
    assert "разблокирована" in response.text
    admin_client.get("/logout")
    response = admin_client.post("/login", data={
        "email": "valeeva@pestrechi-crb.ru", "password": "doctor123",
    })
    assert response.headers["HX-Redirect"] == "/doctor"


def test_doctor_form_validates_required_fields_and_account(admin_client):
    valid = {
        "last_name": "Валеева", "first_name": "Лилия", "specialty": "врач-терапевт",
        "email": "valeeva@pestrechi-crb.ru", "password": "doctor123",
    }
    for change, message in (
        ({"last_name": " "}, "Напишите фамилию и имя"),
        ({"specialty": ""}, "Напишите специальность"),
        ({"email": "@"}, "Проверьте электронную почту"),
        ({"email": " SAFIN@PESTRECHI-CRB.RU "}, "с такой почтой уже есть"),
        ({"password": "short"}, "не короче 8 знаков"),
    ):
        response = admin_client.post("/admin/doctors", data={**valid, **change})
        assert message in response.text and valid["password"] not in response.text


def test_drug_reference_and_patient_cabinet(admin_client):
    response = admin_client.post("/admin/references/drugs", data={"name": "Индопамид"})
    assert "Препарат добавлен" in response.text
    with SessionLocal() as db:
        drug = db.scalar(db.query(Drug).filter(Drug.name == "Индопамид").statement)
        drug_id = drug.id
    response = admin_client.post(f"/admin/references/drugs/{drug_id}", data={"name": "Индопамид ретард"})
    assert "Название препарата сохранено" in response.text

    admin_client.get("/logout")
    admin_client.post("/login", data={"email": "galina.smirnova@mail.ru", "password": DEMO_PASSWORD})
    assert "Индопамид ретард" in admin_client.get("/cabinet").text


def test_drug_reference_validates_rename_and_delete(admin_client):
    with SessionLocal() as db:
        drug_id = db.scalar(select(Drug.id).where(Drug.name == "Амлодипин"))
    for name, message in (
        ("   ", "Напишите название"),
        ("  эНАЛАПРИЛ ", "Такой препарат уже есть"),
        ("А" * 201, "не длиннее 200 знаков"),
    ):
        for url in ("/admin/references/drugs", f"/admin/references/drugs/{drug_id}"):
            assert message in admin_client.post(url, data={"name": name}).text
    response = admin_client.post(f"/admin/references/drugs/{drug_id}", data={"name": "АМЛОДИПИН"})
    assert "Название препарата сохранено" in response.text
    response = admin_client.post(f"/admin/references/drugs/{drug_id}/delete")
    assert "Препарат удалён" in response.text
    with SessionLocal() as db:
        assert db.get(Drug, drug_id) is None


def test_fap_create_update_delete(admin_client):
    response = admin_client.post("/admin/references/faps", data={
        "name": "ФАП test", "village": "test", "address": "ул. Тестовая, 1",
        "latitude": "55,7", "longitude": "49,6",
    })
    assert "ФАП добавлен" in response.text
    with SessionLocal() as db:
        fap = db.scalar(db.query(Fap).filter(Fap.name == "ФАП test").statement)
        assert fap.latitude == 55.7 and fap.longitude == 49.6
        fap_id = fap.id
    response = admin_client.post(f"/admin/references/faps/{fap_id}", data={
        "name": "ФАП test", "village": "test", "address": "ул. Новая, 2",
    })
    assert "Данные ФАП сохранены" in response.text
    response = admin_client.post(f"/admin/references/faps/{fap_id}/delete")
    assert "ФАП удалён" in response.text
    with SessionLocal() as db:
        assert db.get(Fap, fap_id) is None


def test_fap_validates_required_fields_and_coordinates(admin_client):
    valid = {"name": "Название ФАП", "village": "Посёлок", "address": "ул. Тестовая, 1"}
    for change, message in (
        ({"address": ""}, "Заполните название, населённый пункт и адрес"),
        ({"latitude": "не число"}, "Координаты должны быть числами"),
        ({"latitude": "91", "longitude": "49"}, "Широта должна быть от -90 до 90"),
        ({"latitude": "55", "longitude": "181"}, "Долгота должна быть от -180 до 180"),
        ({"latitude": "55"}, "Укажите и широту, и долготу"),
    ):
        for url in ("/admin/references/faps", "/admin/references/faps/1"):
            assert message in admin_client.post(url, data={**valid, **change}).text


def test_migrate_existing_database_adds_doctor_account_link(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE doctors (id INTEGER PRIMARY KEY, name VARCHAR(200), role VARCHAR(100))"))
        connection.execute(text("INSERT INTO doctors (name, role) VALUES ('Сафин', 'терапевт')"))
        connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, doctor_id INTEGER REFERENCES doctors(id))"))
        connection.execute(text("INSERT INTO users (id, doctor_id) VALUES (1, 1)"))
    migrate_schema(engine)
    migrate_schema(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("doctors")}
    assert "user_id" in columns
    assert any(
        index["column_names"] == ["user_id"] and index["unique"]
        for index in inspect(engine).get_indexes("doctors")
    )
    with Session(engine) as db:
        assert db.execute(text("SELECT name, role, user_id FROM doctors")).one() == ("Сафин", "терапевт", None)
        assert db.execute(text("SELECT doctor_id FROM users")).scalar_one() == 1
    engine.dispose()
