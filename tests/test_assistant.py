import re
from dataclasses import replace

import httpx
import pytest
from sqlalchemy import delete, select

from app.config import settings
from app.database import SessionLocal
from app.models import AssistantChat, User
from app.routers import assistant as routes
from app.services import assistant
from app.services.demo import DEMO_PASSWORD

HX = {"HX-Request": "true"}
DISCLAIMER = "Это справка, а не диагноз. Обсудите с врачом"


@pytest.fixture
def configured(monkeypatch):
    config = replace(
        settings, yandex_api_key="test-secret", yandex_folder_id="test-folder",
        yandex_gpt_model="test-model",
    )
    monkeypatch.setattr(routes, "settings", config)
    return config


@pytest.fixture(autouse=True)
def no_live_requests(monkeypatch):
    def reject(*args, **kwargs):
        pytest.fail("Тест не должен обращаться к YandexGPT")

    monkeypatch.setattr(assistant.httpx, "post", reject)


def completion(text="Подготовьте список вопросов к врачу.", status="ALTERNATIVE_STATUS_FINAL"):
    return {"result": {"alternatives": [{"status": status, "message": {"text": text}}]}}


def provider_mock(monkeypatch, payload=None, status_code=200):
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(
            status_code, json=completion() if payload is None else payload,
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(assistant.httpx, "post", post)
    return calls


def token_from(response):
    return re.search(r'name="csrf_token" value="([^"]+)"', response.text)[1]


def ask(client, token, question, **kwargs):
    kwargs.setdefault("headers", HX)
    return client.post("/assistant", data={"csrf_token": token, "question": question}, **kwargs)


def test_page_without_yandex_settings(patient_client, monkeypatch):
    monkeypatch.setattr(routes, "settings", replace(settings, yandex_api_key=""))
    page = patient_client.get("/assistant")
    assert page.status_code == 200
    assert "Помощник пока недоступен" in page.text
    assert 'href="tel:112"' in page.text
    assert 'id="assistant-question" role="status"' in page.text
    assert "<textarea" not in page.text
    assert page.headers["cache-control"] == "no-store"
    assert 'hx-history="false"' in page.text


def test_saves_history_and_uses_only_recent_text(patient_client, configured, monkeypatch):
    calls = provider_mock(monkeypatch)
    page = patient_client.get("/assistant")
    token = token_from(page)
    assert "отправляются в Яндекс" in page.text and "test-secret" not in page.text
    for number in range(5):
        response = ask(patient_client, token, f"Общий вопрос {number}")
        assert response.status_code == 200
        assert "<!doctype html>" not in response.text
        assert response.text.count(DISCLAIMER) == number + 1
    url, request = calls[-1]
    assert url == assistant.COMPLETION_URL
    assert request["headers"]["Authorization"] == "Api-Key test-secret"
    assert request["headers"]["x-data-logging-enabled"] == "false"
    assert request["json"]["modelUri"] == "gpt://test-folder/test-model"
    messages = request["json"]["messages"]
    assert messages[0] == {"role": "system", "text": assistant.SYSTEM_PROMPT}
    assert [entry["text"] for entry in messages if entry["role"] == "user"] == [
        "Общий вопрос 1", "Общий вопрос 2", "Общий вопрос 3", "Общий вопрос 4",
    ]
    for private_value in ("Смирнова", "galina.smirnova", "Эналаприл"):
        assert private_value not in str(messages)
    page = patient_client.get("/assistant")
    assert "Общий вопрос 0" in page.text and page.text.count(DISCLAIMER) == 5
    assert "test-secret" not in page.text


def test_plain_form_redirects_after_success(patient_client, configured, monkeypatch):
    provider_mock(monkeypatch)
    token = token_from(patient_client.get("/assistant"))
    response = ask(patient_client, token, "Что такое диспансеризация?", headers={}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/assistant#assistant-question"
    assert "Что такое диспансеризация?" in patient_client.get("/assistant").text


@pytest.mark.parametrize(("question", "message"), [
    (" ", "Напишите вопрос."),
    ("Как подготовиться к приёму?", "Помощник сейчас недоступен."),
    ("Сильная боль в груди", "Не ждите ответа в чате."),
])
def test_plain_form_keeps_full_page_on_errors_and_local_reply(
    patient_client, configured, monkeypatch, question, message,
):
    provider_mock(monkeypatch, status_code=503)
    token = token_from(patient_client.get("/assistant"))
    response = ask(patient_client, token, question, headers={})
    assert response.status_code == 200
    assert "<!doctype html>" in response.text
    assert 'class="patient-nav patient-nav--bottom"' in response.text
    assert message in response.text


def test_question_and_answer_are_escaped(patient_client, configured, monkeypatch):
    provider_mock(monkeypatch, completion("<script>модель</script>"))
    token = token_from(patient_client.get("/assistant"))
    page = ask(patient_client, token, "<script>пациент</script>")
    assert "&lt;script&gt;модель&lt;/script&gt;" in page.text
    assert "&lt;script&gt;пациент&lt;/script&gt;" in page.text
    assert "<script>" not in page.text and DISCLAIMER in page.text


def test_replaces_recognizable_dose_advice_with_local_reply(patient_client, configured, monkeypatch):
    unsafe = "Выпейте по 2 таблетки."
    question = "Расскажите о приёме лекарств."
    provider_mock(monkeypatch, completion(unsafe))
    token = token_from(patient_client.get("/assistant"))
    response = ask(patient_client, token, question)
    assert unsafe not in response.text
    assert "обратитесь к врачу" in response.text and DISCLAIMER in response.text
    assert f">{question}</textarea>" in response.text
    with SessionLocal() as db:
        assert db.scalar(select(AssistantChat)).exchanges == []


@pytest.mark.parametrize(("question", "message"), [
    ("   ", "Напишите вопрос."),
    ("д" * 601, "Сократите вопрос"),
    ("Моя почта probe@example.org, что такое давление?", "Уберите из вопроса"),
    ("Полис 0000 1111 2222 3333", "Уберите из вопроса"),
    ("Поставь мне диагноз", "обратитесь к врачу"),
    ("Мне плохо, не могу дышать", "Позвонить 112"),
    ("Я хочу умереть", "Позвонить 112"),
])
def test_local_validation_does_not_send_or_store_questions(patient_client, configured, question, message):
    token = token_from(patient_client.get("/assistant"))
    response = ask(patient_client, token, question)
    assert response.status_code == 200 and message in response.text
    assert "Переписка с помощником" not in response.text
    assert response.headers["cache-control"] == "no-store"
    with SessionLocal() as db:
        assert db.scalar(select(AssistantChat)) is None


def test_urgent_answer_works_even_if_yandex_fails(patient_client, configured, monkeypatch):
    token = token_from(patient_client.get("/assistant"))
    monkeypatch.setattr(routes, "settings", replace(configured, yandex_api_key=""))
    response = ask(patient_client, token, "Сильная боль в груди")
    assert 'href="tel:112"' in response.text
    assert "Не ждите ответа" in response.text
    assert DISCLAIMER in response.text


@pytest.mark.parametrize(("status_code", "payload", "exception"), [
    (401, {}, assistant.AssistantUnavailable),
    (403, {}, assistant.AssistantUnavailable),
    (429, {}, assistant.AssistantBusy),
    (503, {}, assistant.AssistantUnavailable),
    (200, {"result": {"alternatives": []}}, assistant.AssistantUnavailable),
    (200, completion(None), assistant.AssistantUnavailable),
    (200, completion(" "), assistant.AssistantUnavailable),
    (200, completion("д" * (assistant.MAX_ANSWER_LENGTH + 1)), assistant.AssistantUnavailable),
    (200, completion(status="ALTERNATIVE_STATUS_PARTIAL"), assistant.AssistantUnavailable),
    (200, completion(status="ALTERNATIVE_STATUS_TRUNCATED_FINAL"), assistant.AssistantUnavailable),
    (200, completion(status="ALTERNATIVE_STATUS_CONTENT_FILTER"), assistant.AssistantFiltered),
])
def test_provider_errors_never_return_unfinished_answer(configured, monkeypatch, status_code, payload, exception):
    calls = provider_mock(monkeypatch, payload, status_code)
    with pytest.raises(exception) as error:
        assistant.make_answer(configured, "Общий вопрос", [])
    assert type(error.value) is exception
    assert len(calls) == 1


def test_timeout_keeps_draft_and_does_not_retry(patient_client, configured, monkeypatch):
    attempts = []

    def timed_out(url, **kwargs):
        attempts.append(kwargs["timeout"])
        raise httpx.ReadTimeout("private error")

    monkeypatch.setattr(assistant.httpx, "post", timed_out)
    token = token_from(patient_client.get("/assistant"))
    response = ask(patient_client, token, "Как подготовиться к визиту?")
    assert "Помощник не успел ответить" in response.text
    assert "перед повторной отправкой" in response.text
    assert ">Как подготовиться к визиту?</textarea>" in response.text
    assert "private error" not in response.text and len(attempts) == 1
    assert "Как подготовиться к визиту?" not in patient_client.get("/assistant").text


@pytest.mark.parametrize("failure", ["timeout", 429, 503])
def test_manual_retry_after_provider_failure_saves_once(patient_client, configured, monkeypatch, failure):
    attempts = []
    earlier = "Как проходит обычный приём?"
    question = "Что взять на приём?"

    def post(url, **kwargs):
        attempts.append(kwargs["json"]["messages"])
        request = httpx.Request("POST", url)
        if len(attempts) == 2:
            if failure == "timeout":
                raise httpx.ReadTimeout("private error", request=request)
            return httpx.Response(failure, request=request)
        return httpx.Response(200, json=completion(), request=request)

    monkeypatch.setattr(assistant.httpx, "post", post)
    token = token_from(patient_client.get("/assistant"))
    assert ask(patient_client, token, earlier).text.count(DISCLAIMER) == 1

    failed = ask(patient_client, token, question)
    assert 'id="assistant-error"' in failed.text
    assert f">{question}</textarea>" in failed.text
    assert len(attempts) == 2
    page = patient_client.get("/assistant").text
    assert earlier in page and question not in page

    retried = ask(patient_client, token_from(failed), question)
    assert retried.text.count(DISCLAIMER) == 2
    assert f">{question}</textarea>" not in retried.text
    assert 'id="assistant-error"' not in retried.text
    assert len(attempts) == 3 and attempts[1] == attempts[2]
    with SessionLocal() as db:
        history = db.scalar(select(AssistantChat)).exchanges
        assert [exchange["question"] for exchange in history] == [earlier, question]


def test_keeps_draft_when_conversation_changes_during_request(patient_client, configured, monkeypatch):
    def answer_after_clear(settings, question, history):
        with SessionLocal() as db:
            patient_id = db.scalar(select(User.id).where(User.email == "galina.smirnova@mail.ru"))
            assistant.clear_history(db, patient_id)
        return "Запоздавший ответ."

    monkeypatch.setattr(assistant, "make_answer", answer_after_clear)
    token = token_from(patient_client.get("/assistant"))
    response = ask(patient_client, token, "Что взять на приём?")
    assert "Переписка изменилась." in response.text
    assert ">Что взять на приём?</textarea>" in response.text
    assert "Запоздавший ответ" not in response.text
    assert "Что взять на приём?" not in patient_client.get("/assistant").text


def test_stale_htmx_form_updates_only_feedback_without_asking_or_clearing(
    patient_client, configured, monkeypatch,
):
    calls = provider_mock(monkeypatch)
    token = token_from(patient_client.get("/assistant"))
    ask(patient_client, token, "Сохранённый вопрос")
    stale_ask = ask(patient_client, "old-token", "Неотправленный черновик")
    stale_clear = patient_client.post("/assistant/clear", data={"csrf_token": "old-token"}, headers=HX)
    for response in (stale_ask, stale_clear):
        assert response.status_code == 200
        assert response.headers["HX-Retarget"] == "#assistant-recovery"
        assert "Страница устарела" in response.text
        assert "<textarea" not in response.text and "Сохранённый вопрос" not in response.text
        assert "Неотправленный черновик" not in response.text
    assert len(calls) == 1
    assert "Сохранённый вопрос" in patient_client.get("/assistant").text


def test_stale_plain_form_keeps_draft_and_does_not_send(patient_client, configured):
    token = token_from(patient_client.get("/assistant"))
    response = ask(patient_client, "old-token", "Мой черновик", headers={})
    assert response.status_code == 403
    assert "<!doctype html>" in response.text
    assert token_from(response) == token
    assert "Форма устарела" in response.text
    assert ">Мой черновик</textarea>" in response.text


def test_requires_active_patient(client, configured):
    for url in ("/assistant", "/assistant/clear"):
        assert client.post(url, follow_redirects=False).headers["location"] == "/login"
    assert client.get("/assistant", follow_redirects=False).headers["location"] == "/login"
    for email, expected in (("petrov@mail.ru", 303), ("safin@pestrechi-crb.ru", 403),
                            ("admin@pestrechi-crb.ru", 403)):
        client.post("/login", data={"email": email, "password": DEMO_PASSWORD})
        assert client.get("/assistant", follow_redirects=False).status_code == expected
        for url in ("/assistant", "/assistant/clear"):
            assert client.post(url, follow_redirects=False).status_code == expected


def test_history_and_clear_are_private(patient_client, configured, monkeypatch):
    calls = provider_mock(monkeypatch)
    first_token = token_from(patient_client.get("/assistant"))
    ask(patient_client, first_token, "Первый вопрос")
    with SessionLocal.begin() as db:
        other = db.scalar(select(User).where(User.email == "petrov@mail.ru"))
        other.status = "active"
    patient_client.post("/login", data={"email": "petrov@mail.ru", "password": DEMO_PASSWORD})
    other_page = patient_client.get("/assistant")
    other_token = token_from(other_page)
    assert "Первый вопрос" not in other_page.text
    assert ask(patient_client, first_token, "Не должно отправиться", headers={}).status_code == 403
    data = {"csrf_token": first_token}
    assert patient_client.post("/assistant/clear", data=data).status_code == 403
    ask(patient_client, other_token, "Второй вопрос")
    assert "Первый вопрос" not in str(calls[-1][1]["json"])
    cleared = patient_client.post("/assistant/clear", data={"csrf_token": other_token}, headers=HX)
    assert "Переписка удалена." in cleared.text and "Второй вопрос" not in cleared.text
    assert "Второй вопрос" not in patient_client.get("/assistant").text
    patient_client.post("/login", data={"email": "galina.smirnova@mail.ru", "password": DEMO_PASSWORD})
    assert "Первый вопрос" in patient_client.get("/assistant").text


def test_retention_and_clear_win_over_pending_request(client):
    with SessionLocal() as db:
        patient_id = db.scalar(select(User.id).where(User.email == "galina.smirnova@mail.ru"))
        for number in range(assistant.MAX_SAVED_EXCHANGES + 1):
            revision, history = assistant.start_exchange(db, patient_id)
            assert assistant.save_exchange(db, patient_id, revision, history, str(number), "ответ")
        revision, history = assistant.start_exchange(db, patient_id)
        assert len(history) == assistant.MAX_SAVED_EXCHANGES and history[0]["question"] == "1"
        with SessionLocal() as another_session:
            assistant.clear_history(another_session, patient_id)
        assert not assistant.save_exchange(db, patient_id, revision, history, "запоздалый вопрос", "ответ")
        assert assistant.read_history(db, patient_id) == []


def test_account_deletion_removes_assistant_history(client):
    with SessionLocal.begin() as db:
        user = User(email="history-delete@example.test", password_hash="unused")
        db.add(user)
        db.flush()
        patient_id = user.id
        db.add(AssistantChat(
            patient_id=patient_id, exchanges=[{"question": "Тестовый вопрос", "answer": "Тестовый ответ"}],
        ))
    with SessionLocal.begin() as db:
        assert db.get(AssistantChat, patient_id) is not None
        db.execute(delete(User).where(User.id == patient_id))
    with SessionLocal() as db:
        assert db.get(AssistantChat, patient_id) is None
