import os
import re
from base64 import b64decode
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from threading import Event
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
import qrcode
from fastapi.testclient import TestClient
from qrcode.image.svg import SvgPathFillImage
from sqlalchemy import create_engine, delete, func, select, text, update
from sqlalchemy.engine import make_url
from starlette.websockets import WebSocketDisconnect

from app import main
from app.database import SessionLocal, migrate_schema
from app.main import app
from app.models import Doctor, DoctorMessage, InviteCode, LoginSession, User
from app.routers import doctor, doctor_chat
from app.services import chat_events
from app.services.demo import DEMO_DOCTOR, DEMO_PASSWORD, DEMO_PATIENT, seed_demo_data
from app.services.doctor_chat import MAX_MESSAGE_LENGTH, PAGE_SIZE
from app.services.security import hash_password

OTHER_DOCTOR = "garipova@pestrechi-crb.ru"
ADMIN = "admin@pestrechi-crb.ru"
ORIGIN = {"origin": "http://testserver"}
FIRST_MESSAGE = "Где посмотреть расписание приёма лекарств?"
POSTGRES_URL = os.environ.get("PULSE_TEST_POSTGRES_URL")


def _login(client, email, target):
    result = client.post("/login", data={"email": email, "password": DEMO_PASSWORD})
    assert result.headers["HX-Redirect"] == target
    return client


@contextmanager
def _postgres_schema():
    url = make_url(POSTGRES_URL)
    assert url.drivername == "postgresql+psycopg"
    schema = f"pulse_test_{uuid4().hex}"
    bootstrap = create_engine(url)
    try:
        with bootstrap.begin() as conn:
            conn.exec_driver_sql(f"CREATE SCHEMA {schema}")
        try:
            options = url.query.get("options", "")
            yield url.update_query_dict({
                "options": f"{options} -csearch_path={schema}".strip(),
                "application_name": schema,
            })
        finally:
            with bootstrap.begin() as conn:
                conn.exec_driver_sql(f"DROP SCHEMA {schema} CASCADE")
    finally:
        bootstrap.dispose()


@contextmanager
def _test_client(url, monkeypatch):
    engine = create_engine(url)
    monkeypatch.setitem(SessionLocal.kw, "bind", engine)
    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setattr(main, "migrate_schema", lambda: migrate_schema(engine))
    try:
        with TestClient(app) as test_client:
            with SessionLocal() as db:
                seed_demo_data(db)
            yield test_client
    finally:
        engine.dispose()


@pytest.fixture(params=["sqlite", "postgresql"] if POSTGRES_URL else ["sqlite"])
def client(request, tmp_path, monkeypatch):
    if request.param == "postgresql":
        with _postgres_schema() as url, _test_client(url, monkeypatch) as test_client:
            yield test_client
    else:
        # Два WebSocket одновременно не должны делить одно соединение StaticPool из общей фикстуры.
        with _test_client(f"sqlite:///{(tmp_path / 'doctor.db').as_posix()}", monkeypatch) as test_client:
            yield test_client


@pytest.fixture
def patient_id(client):
    with SessionLocal() as db:
        return db.scalar(select(User.id).where(User.email == DEMO_PATIENT))


def _csrf(page):
    return re.search(r'name="csrf_token" value="([^"]+)"', page.text)[1]


def _submission(page):
    return re.search(r'name="submission_id" value="([a-f0-9]{32})"', page)[1]


def _message_form(page, message):
    return {
        "message": message, "submission_id": _submission(page.text), "csrf_token": _csrf(page),
        "doctor_id": re.search(r'name="doctor_id" value="(\d+)"', page.text)[1],
    }


def _send(client, patient_id, form):
    return client.post(f"/doctor-chat/{patient_id}/messages", data=form, headers={"HX-Request": "true"})


def test_cabinet_shows_assigned_patient_and_schedule_only(client, patient_id):
    assert client.get("/doctor", follow_redirects=False).headers["location"] == "/login"
    physician = _login(client, DEMO_DOCTOR, "/doctor")
    page = physician.get("/doctor")
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    for text in ("Кабинет врача", "Мои пациенты", "Смирнова Галина", "Эналаприл", "08:00",
                 "Принято в 08:05", "Нет отметки", "Чат с пациентом", FIRST_MESSAGE):
        assert text in page.text
    assert "Петров Олег" not in page.text and "полис" not in page.text
    assert physician.get(f"/doctor/patients/{patient_id}").status_code == 200
    assert physician.get("/doctor/patients/99999").status_code == 404
    colleague = _login(TestClient(app), OTHER_DOCTOR, "/doctor")
    assert "За вами пока нет закреплённых пациентов" in colleague.get("/doctor").text
    assert colleague.get(f"/doctor/patients/{patient_id}").status_code == 404
    assert colleague.get(f"/doctor-chat/history/{patient_id}").status_code == 404
    patient = _login(TestClient(app), DEMO_PATIENT, "/")
    assert patient.get("/doctor").status_code == 403
    chat = patient.get("/doctor-chat")
    assert "Сафин Ильдар" in chat.text and 'href="tel:112"' in chat.text
    assert "htmx-ws.min.js" in chat.text and chat.headers["cache-control"] == "no-store"


def test_chat_delivers_both_ways_persists_and_deduplicates(client, patient_id, monkeypatch):
    def unexpected_poll(*args):
        pytest.fail("Доставка должна произойти по событию, а не по резервной сверке.")

    monkeypatch.setattr(doctor_chat, "_room_version", unexpected_poll)
    patient = _login(client, DEMO_PATIENT, "/")
    physician = _login(TestClient(app), DEMO_DOCTOR, "/doctor")
    path = f"/doctor-chat/ws/{patient_id}"
    question = _message_form(patient.get("/doctor-chat"), ' <b>Вопрос пациента</b> ')
    answer = _message_form(physician.get("/doctor"), "Ответ врача")
    with patient.websocket_connect(path, headers=ORIGIN) as p, physician.websocket_connect(path, headers=ORIGIN) as d:
        assert FIRST_MESSAGE in p.receive_text()
        assert FIRST_MESSAGE in d.receive_text()
        acknowledgement = _send(patient, patient_id, question).text
        update = p.receive_text()
        assert "&lt;b&gt;Вопрос пациента&lt;/b&gt;" in update
        assert "<b>Вопрос пациента</b>" not in update
        assert "Сообщение отправлено" in acknowledgement
        assert _submission(acknowledgement) != question["submission_id"]
        assert "Вопрос пациента" in d.receive_text()
        assert "Сообщение отправлено" in _send(physician, patient_id, answer).text
        assert "Ответ врача" in d.receive_text()
        assert "Ответ врача" in p.receive_text()

    with patient.websocket_connect(path, headers=ORIGIN) as p:
        initial = p.receive_text()
        assert "Вопрос пациента" in initial and "Ответ врача" in initial
    assert "Сообщение отправлено" in _send(patient, patient_id, question).text
    assert "Ответ врача" in patient.get("/doctor-chat").text
    assert "Вопрос пациента" in physician.get("/doctor").text
    with SessionLocal() as db:
        rows = list(db.scalars(select(DoctorMessage).where(DoctorMessage.submission_id.in_(
            [question["submission_id"], answer["submission_id"]],
        )).order_by(DoctorMessage.id)))
        assert [(row.sender_role, row.text) for row in rows] == [
            ("patient", "<b>Вопрос пациента</b>"), ("doctor", "Ответ врача"),
        ]


@pytest.mark.parametrize(("payload", "expected"), [
    ({"message": "  \n "}, "Напишите сообщение"),
    ({"message": "а" * (MAX_MESSAGE_LENGTH + 1)}, "Сократите сообщение"),
    ({"message": "Ответ", "submission_id": "not-a-valid-id"}, "Форма устарела"),
    ({"message": "слово\0слово"}, "Уберите необычные символы"),
])
def test_chat_reports_invalid_messages_without_saving(client, patient_id, payload, expected):
    patient = _login(client, DEMO_PATIENT, "/")
    with SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(DoctorMessage))
    form = {**_message_form(patient.get("/doctor-chat"), ""), **payload}
    response = _send(patient, patient_id, form)
    assert expected in response.text and "Сообщение отправлено" not in response.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(DoctorMessage)) == before


def test_chat_second_text_with_same_submission_id_preserves_draft(client, patient_id):
    patient = _login(client, DEMO_PATIENT, "/")
    page = patient.get("/doctor-chat")
    assert 'hx-disabled-elt="find textarea, find button"' in page.text
    assert 'hx-sync="#doctor-chat-composer:drop"' in page.text
    form = _message_form(page, "Первое сообщение")
    assert "Сообщение отправлено" in _send(patient, patient_id, form).text
    second = {**form, "message": "Второе сообщение"}
    conflicting = _send(patient, patient_id, second)
    assert "Этот текст не отправлен" in conflicting.text
    assert ">\nВторое сообщение</textarea>" in conflicting.text
    assert _submission(conflicting.text) != form["submission_id"]
    assert "Второе сообщение" not in patient.get("/doctor-chat").text
    retry = {**second, "submission_id": _submission(conflicting.text)}
    assert "Сообщение отправлено" in _send(patient, patient_id, retry).text
    history = patient.get("/doctor-chat").text
    assert history.count("Первое сообщение") == history.count("Второе сообщение") == 1


def test_chat_fallback_reads_messages_only_when_version_changes(client, patient_id, monkeypatch):
    monkeypatch.setattr(chat_events, "_wake", lambda *args: None)
    monkeypatch.setattr(doctor_chat, "_CHECK_INTERVAL", 0.1)
    patient = _login(client, DEMO_PATIENT, "/")
    form = _message_form(patient.get("/doctor-chat"), "Доставка без сигнала")
    with SessionLocal.begin() as db:
        db.add_all(DoctorMessage(
            patient_id=patient_id, sender_id=patient_id, sender_role="patient",
            submission_id=uuid4().hex, text=f"История чата [{i}]",
            sent_at=datetime.now(UTC).replace(tzinfo=None),
        ) for i in range(PAGE_SIZE + 2))
    real_room, real_version = doctor_chat._room, doctor_chat._room_version
    checked = Event()
    history_reads = []

    def read(*args):
        history_reads.append(True)
        return real_room(*args)

    def check(*args):
        version = real_version(*args)
        checked.set()
        return version

    monkeypatch.setattr(doctor_chat, "_room", read)
    monkeypatch.setattr(doctor_chat, "_room_version", check)
    with patient.websocket_connect(f"/doctor-chat/ws/{patient_id}", headers=ORIGIN) as socket:
        initial = socket.receive_text()
        moved = "История чата [2]"
        assert moved in initial and FIRST_MESSAGE not in initial
        assert initial.count('class="doctor-chat-message__text"') == PAGE_SIZE
        assert checked.wait(3) and len(history_reads) == 1
        assert "Сообщение отправлено" in _send(patient, patient_id, form).text
        refreshed = socket.receive_text()
        assert "Доставка без сигнала" in refreshed and moved not in refreshed
        assert refreshed.count('class="doctor-chat-message__text"') == PAGE_SIZE
        older = re.search(r'href="(/doctor-chat/history/\d+\?before=\d+)"', refreshed)[1]
        assert older not in initial
        older_page = patient.get(older)
        assert moved in older_page.text and FIRST_MESSAGE in older_page.text
        assert len(history_reads) == 2


@pytest.mark.skipif(not POSTGRES_URL, reason="Нужна PostgreSQL: PULSE_TEST_POSTGRES_URL")
def test_postgresql_listener_reconnects_and_catches_up(monkeypatch):
    # Тест запускает свой экземпляр приложения после установки наблюдателей.
    listener_ready, listener_down, resume = Event(), Event(), Event()
    real_wake = chat_events._wake

    def wake(patient_id=None):
        if patient_id is None:
            listener_ready.set()
        real_wake(patient_id)

    class ControlledStop(Event):
        def wait(self, timeout=None):
            listener_down.set()
            resume.wait(10)
            return self.is_set()

    def unexpected_poll(*args):
        pytest.fail("Пропущенное сообщение должен доставить повторный LISTEN, а не резервная сверка.")

    monkeypatch.setattr(chat_events, "_wake", wake)
    monkeypatch.setattr(chat_events, "Event", ControlledStop)
    monkeypatch.setattr(doctor_chat, "_CHECK_INTERVAL", 10)
    monkeypatch.setattr(doctor_chat, "_room_version", unexpected_poll)
    with _postgres_schema() as url, _test_client(url, monkeypatch) as test_client:
        try:
            assert listener_ready.wait(5)
            patient = _login(test_client, DEMO_PATIENT, "/")
            with SessionLocal() as db:
                patient_id = db.scalar(select(User.id).where(User.email == DEMO_PATIENT))
            path = f"/doctor-chat/ws/{patient_id}"
            form = _message_form(patient.get("/doctor-chat"), "Сообщение в паузе LISTEN")

            with patient.websocket_connect(path, headers=ORIGIN) as socket:
                assert FIRST_MESSAGE in socket.receive_text()
                with SessionLocal.begin() as db:
                    # Останавливаем только LISTEN-соединение этой изолированной схемы.
                    pid = db.execute(text("""
                        SELECT pid FROM pg_stat_activity
                        WHERE datname = current_database() AND application_name = :app_name
                          AND query = :listen_query AND pid <> pg_backend_pid()
                    """), {"app_name": url.query["application_name"],
                           "listen_query": f"LISTEN {chat_events._CHANNEL}"}).scalar_one()
                    assert db.scalar(select(func.pg_terminate_backend(pid))) is True
                assert listener_down.wait(5)
                listener_ready.clear()
                acknowledgement = _send(patient, patient_id, form)
                assert "Сообщение отправлено" in acknowledgement.text
                resume.set()
                assert listener_ready.wait(5)
                assert "Сообщение в паузе LISTEN" in socket.receive_text()

                next_form = _message_form(acknowledgement, "Следующее сообщение после LISTEN")
                assert "Сообщение отправлено" in _send(patient, patient_id, next_form).text
                assert "Следующее сообщение после LISTEN" in socket.receive_text()
        finally:
            resume.set()


def test_chat_send_rejects_bad_csrf_and_other_patient(client, patient_id):
    patient = _login(client, DEMO_PATIENT, "/")
    form = _message_form(patient.get("/doctor-chat"), "Не отправлять")
    assert _send(patient, patient_id, {**form, "csrf_token": "wrong"}).status_code == 403
    denied = _send(patient, patient_id + 1, form)
    assert "Доступ к чату изменился" in denied.text and "Сообщение отправлено" not in denied.text


def test_chat_denies_other_sessions_and_origins(client, patient_id):
    path = f"/doctor-chat/ws/{patient_id}"
    with SessionLocal.begin() as db:
        outsider = User(
            email="other-patient@example.test", password_hash=hash_password(DEMO_PASSWORD), role="patient",
            last_name="Тестовый", doctor_id=db.get(User, patient_id).doctor_id,
        )
        db.add(outsider)
    for visitor, headers in (
        (client, ORIGIN),
        (_login(TestClient(app), OTHER_DOCTOR, "/doctor"), ORIGIN),
        (_login(TestClient(app), ADMIN, "/admin"), ORIGIN),
        (_login(TestClient(app), "other-patient@example.test", "/"), ORIGIN),
        (_login(TestClient(app), DEMO_PATIENT, "/"), {"origin": "https://other.example"}),
        (_login(TestClient(app), DEMO_PATIENT, "/"), {}),
    ):
        with pytest.raises(WebSocketDisconnect) as closed, visitor.websocket_connect(path, headers=headers):
            pytest.fail("Сокет не должен открываться")
        assert closed.value.code == 1008
    outsider = _login(TestClient(app), "other-patient@example.test", "/")
    assert outsider.get(f"/doctor-chat/history/{patient_id}").status_code == 404


@pytest.mark.parametrize("reason", ["logout", "blocked", "reassigned"])
def test_open_chat_loses_access_after_logout_block_or_reassignment(client, patient_id, reason, monkeypatch):
    monkeypatch.setattr(doctor_chat, "_CHECK_INTERVAL", 0.2)
    physician = _login(client, DEMO_DOCTOR, "/doctor")
    colleague = _login(TestClient(app), OTHER_DOCTOR, "/doctor")
    admin = _login(TestClient(app), ADMIN, "/admin")
    cookie = physician.cookies["pulse_session"]
    form = _message_form(physician.get("/doctor"), "Не должно сохраниться")
    with SessionLocal() as db:
        old_doctor_id = db.get(User, patient_id).doctor_id
        new_doctor_id = db.scalar(select(Doctor.id).join(Doctor.account).where(User.email == OTHER_DOCTOR))
    path = f"/doctor-chat/ws/{patient_id}"
    with physician.websocket_connect(path, headers=ORIGIN) as socket:
        assert FIRST_MESSAGE in socket.receive_text()
        if reason == "logout":
            physician.get("/logout")
        elif reason == "blocked":
            admin.post(f"/admin/doctors/{old_doctor_id}/status", data={"status": "blocked"})
        else:
            admin.post(f"/admin/patients/{patient_id}/doctor", data={"doctor_id": str(new_doctor_id)})
        revoked = socket.receive_text()
        assert "Доступ к чату изменился" in revoked and FIRST_MESSAGE not in revoked
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_text()
        assert closed.value.code == 1008

    physician.cookies.set("pulse_session", cookie)
    denied = _send(physician, patient_id, form)
    assert "Сообщение отправлено" not in denied.text
    with SessionLocal() as db:
        assert not db.scalar(select(DoctorMessage.id).where(DoctorMessage.text == "Не должно сохраниться"))
    if reason == "logout":
        assert physician.get("/doctor", follow_redirects=False).headers["location"] == "/login"
    if reason == "reassigned":
        assert physician.get(f"/doctor/patients/{patient_id}").status_code == 404
        assert FIRST_MESSAGE in colleague.get(f"/doctor/patients/{patient_id}").text
        with colleague.websocket_connect(path, headers=ORIGIN) as socket:
            assert FIRST_MESSAGE in socket.receive_text()


@pytest.mark.parametrize(("email", "home", "chat_page"), [
    (DEMO_PATIENT, "/", "/doctor-chat"),
    (DEMO_DOCTOR, "/doctor", "/doctor"),
])
def test_expired_session_closes_chat_but_preserves_draft(client, patient_id, monkeypatch, email, home, chat_page):
    monkeypatch.setattr(doctor_chat, "_CHECK_INTERVAL", 0.2)
    participant = _login(client, email, home)
    form = _message_form(participant.get(chat_page), "\n<Черновик после окончания сеанса>")
    path = f"/doctor-chat/ws/{patient_id}"
    with participant.websocket_connect(path, headers=ORIGIN) as socket:
        assert FIRST_MESSAGE in socket.receive_text()
        with SessionLocal.begin() as db:
            user_id = db.scalar(select(User.id).where(User.email == email))
            db.execute(update(LoginSession).where(LoginSession.user_id == user_id).values(
                expires_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1),
            ))
        revoked = socket.receive_text()
        assert "Доступ к чату изменился" in revoked and FIRST_MESSAGE not in revoked
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_text()
        assert closed.value.code == 1008
    with pytest.raises(WebSocketDisconnect), participant.websocket_connect(path, headers=ORIGIN):
        pytest.fail("Истёкшая сессия не должна открывать WebSocket")

    for url in {home, chat_page, f"/doctor-chat/history/{patient_id}"}:
        redirect = participant.get(url, follow_redirects=False)
        assert redirect.status_code == 303 and redirect.headers["location"] == "/login"
    denied = _send(participant, patient_id, form)
    assert denied.status_code == 200 and "HX-Redirect" not in denied.headers
    assert "Сообщение не отправлено" in denied.text and "readonly" in denied.text
    assert 'href="/login" target="_blank"' in denied.text
    assert ">\n\n&lt;Черновик после окончания сеанса&gt;</textarea>" in denied.text
    assert "<form" not in denied.text and FIRST_MESSAGE not in denied.text
    native = participant.post(f"/doctor-chat/{patient_id}/messages", data=form, follow_redirects=False)
    assert native.status_code == 303 and native.headers["location"] == "/login"
    with SessionLocal() as db:
        assert db.scalar(select(DoctorMessage.id).where(DoctorMessage.submission_id == form["submission_id"])) is None


def test_patient_history_is_paginated_and_readable_without_available_doctor(client, patient_id):
    with SessionLocal.begin() as db:
        patient = db.get(User, patient_id)
        patient.doctor_id = None
        db.add_all(DoctorMessage(
            patient_id=patient_id, sender_id=patient_id, sender_role="patient",
            submission_id=uuid4().hex, text=f"Проверка истории №{i}",
            sent_at=datetime.now(UTC).replace(tzinfo=None),
        ) for i in range(PAGE_SIZE + 2))
    patient = _login(client, DEMO_PATIENT, "/")
    page = patient.get("/doctor-chat")
    assert "Сейчас написать врачу нельзя" in page.text and "ws-connect" not in page.text
    assert FIRST_MESSAGE not in page.text
    older_link = re.search(r'href="(/doctor-chat/history/\d+\?before=\d+)"', page.text)[1]
    previous_page = patient.get(older_link)
    assert FIRST_MESSAGE in previous_page.text and "История переписки" in previous_page.text


def test_patient_draft_cannot_be_sent_to_a_new_doctor_from_an_old_chat(client, patient_id, monkeypatch):
    monkeypatch.setattr(doctor_chat, "_CHECK_INTERVAL", 0.2)
    patient = _login(client, DEMO_PATIENT, "/")
    admin = _login(TestClient(app), ADMIN, "/admin")
    before = patient.get("/doctor-chat")
    path = re.search(r'ws-connect="([^"]+)"', before.text)[1]
    with SessionLocal() as db:
        new_doctor_id = db.scalar(select(Doctor.id).join(Doctor.account).where(User.email == OTHER_DOCTOR))
    with patient.websocket_connect(path, headers=ORIGIN) as socket:
        assert FIRST_MESSAGE in socket.receive_text()
        admin.post(f"/admin/patients/{patient_id}/doctor", data={"doctor_id": str(new_doctor_id)})
        assert "Доступ к чату изменился" in socket.receive_text()
        with pytest.raises(WebSocketDisconnect):
            socket.receive_text()
    with pytest.raises(WebSocketDisconnect), patient.websocket_connect(path, headers=ORIGIN):
        pytest.fail("Страница со старым врачом не должна переподключаться")
    denied = _send(patient, patient_id, _message_form(before, "Черновик для прошлого врача"))
    assert denied.status_code == 200 and "HX-Redirect" not in denied.headers
    assert ">\nЧерновик для прошлого врача</textarea>" in denied.text and "readonly" in denied.text
    assert "<form" not in denied.text and "Сообщение отправлено" not in denied.text
    current = patient.get("/doctor-chat")
    assert "Гарипова Алсу" in current.text and FIRST_MESSAGE in current.text
    assert "Черновик для прошлого врача" not in current.text


def test_patient_deletion_clears_chat_history_and_sessions(client):
    with SessionLocal.begin() as db:
        doctor_id = db.scalar(select(Doctor.id).join(Doctor.account).where(User.email == DEMO_DOCTOR))
        patient = User(
            email="cleanup-patient@example.test", password_hash=hash_password(DEMO_PASSWORD), role="patient",
            last_name="Тестовый", doctor_id=doctor_id,
        )
        db.add(patient)
        db.flush()
        patient_id = patient.id
    patient = _login(client, "cleanup-patient@example.test", "/")
    form = _message_form(patient.get("/doctor-chat"), "Переписка удаляемого пациента")
    assert "Сообщение отправлено" in _send(patient, patient_id, form).text
    with SessionLocal.begin() as db:
        db.execute(delete(User).where(User.id == patient_id))
    with SessionLocal() as db:
        assert db.scalar(select(LoginSession.id).where(LoginSession.user_id == patient_id)) is None
        assert db.scalar(select(DoctorMessage.id).where(DoctorMessage.patient_id == patient_id)) is None


def test_doctor_creates_printable_one_time_invite_and_registration_binds_patient(client, monkeypatch):
    monkeypatch.setattr(doctor, "settings", type("Settings", (), {"base_url": "https://pulse.example.test/"})())
    physician = _login(client, DEMO_DOCTOR, "/doctor")
    form = physician.get("/doctor/invites")
    assert "PLS-4827" in form.text and "PLS-3159" not in form.text
    assert physician.post("/doctor/invites", data={"patient_hint": "Тестовый"}).status_code == 403
    csrf = _csrf(form)
    assert "Напишите фамилию" in physician.post("/doctor/invites", data={"csrf_token": csrf}).text
    created = physician.post("/doctor/invites", data={
        "patient_hint": "Кузнецов Андрей Викторович", "csrf_token": csrf, "doctor_id": "2",
    })
    assert created.status_code == 200 and created.headers["cache-control"] == "no-store"
    code = re.search(r'class="doctor-print-code__value">(PLS-\d{4})<', created.text)[1]
    url = f"https://pulse.example.test/register?code={code}"
    assert f'href="{url}"' in created.text and "Сафин Ильдар" in created.text
    assert "Кузнецов Андрей" not in created.text and "Ctrl" in created.text
    embedded = re.search(r'src="data:image/svg\+xml;base64,([^"]+)"', created.text)[1]
    assert b64decode(embedded) == qrcode.make(url, image_factory=SvgPathFillImage).to_string()
    register_url = urlsplit(url)
    visitor = TestClient(app)
    prefilled = visitor.get(f"{register_url.path}?{register_url.query}")
    assert f'value="{code}"' in prefilled.text and "Сафин Ильдар" in prefilled.text
    assert prefilled.headers["referrer-policy"] == "no-referrer"
    form = {
        "code": code, "last_name": "Кузнецов", "first_name": "Андрей", "birth_date": "1955-01-02",
        "phone": "+7 917 111-22-33", "email": "visitor@example.test", "consent": "1",
        "password": "test-pass123", "password2": "test-pass123",
    }
    assert visitor.post("/register", data=form).headers["HX-Redirect"] == "/cabinet?welcome=1"
    assert "уже зарегистрировались" in visitor.post("/register", data={**form, "email": "other@example.test"}).text
    with SessionLocal() as db:
        invite = db.scalar(select(InviteCode).where(InviteCode.code == code))
        assert invite.doctor.account.email == DEMO_DOCTOR and invite.used_by.doctor_id == invite.doctor_id
        assert invite.expires_at - invite.issued_at == timedelta(days=30)
        assert invite.status == "used"
    assert "Распечатать можно только действующий код" in physician.get(created.url.path).text


def test_doctor_can_revoke_own_active_invite_but_not_colleagues(client):
    physician = _login(client, DEMO_DOCTOR, "/doctor")
    colleague = _login(TestClient(app), OTHER_DOCTOR, "/doctor")
    csrf = _csrf(physician.get("/doctor/invites"))
    colleague_csrf = _csrf(colleague.get("/doctor/invites"))
    assert colleague.get("/doctor/invites/1/print").status_code == 404
    assert colleague.post("/doctor/invites/1/revoke", data={"csrf_token": colleague_csrf}).status_code == 404
    assert physician.post("/doctor/invites/1/revoke", data={"csrf_token": "wrong"}).status_code == 403
    result = physician.post("/doctor/invites/1/revoke", data={"csrf_token": csrf})
    assert "Код PLS-4827 отменён" in result.text and "Отменён" in result.text
    assert "Распечатать можно только действующий код" in physician.get("/doctor/invites/1/print").text
    assert "Можно отменить только действующий код" in physician.post(
        "/doctor/invites/1/revoke", data={"csrf_token": csrf},
    ).text
    assert "Срок действия кода закончился" in TestClient(app).get("/register?code=PLS-4827").text
