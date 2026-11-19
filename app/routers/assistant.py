"""Страница справочного ИИ-помощника для активного пациента."""
import hmac
import secrets

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.access import require_roles
from app.config import settings
from app.database import get_db
from app.models import User
from app.services import assistant
from app.web import templates

router = APIRouter(prefix="/assistant")
_patient = require_roles("patient")
_PRIVATE_HEADERS = {"Cache-Control": "no-store", "Vary": "HX-Request"}
_UNAVAILABLE = "Помощник сейчас недоступен. Попробуйте позже. Если вопрос не может ждать, обратитесь к врачу."
_REFER_TO_DOCTOR = "Я не могу поставить диагноз или подобрать лечение. С этим вопросом обратитесь к врачу."
_STALE_FORM = "Страница устарела. Если вы набрали вопрос, скопируйте его перед обновлением."


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _valid_token(request: Request, token: str) -> bool:
    expected = request.session.get("assistant_csrf")
    return bool(expected and token.isascii() and hmac.compare_digest(expected, token))


def _render(request: Request, db: Session, patient_id: int, *, status_code: int = 200, **extra):
    csrf_token = request.session.get("assistant_csrf")
    if not csrf_token:
        csrf_token = request.session["assistant_csrf"] = secrets.token_urlsafe(24)
    template = (
        "partials/assistant_chat.html"
        if request.method == "POST" and _is_htmx(request) else "patient/assistant.html"
    )
    return templates.TemplateResponse(request, template, {
        "active_tab": "assistant",
        "csrf_token": csrf_token,
        "history": assistant.read_history(db, patient_id),
        "available": assistant.configured(settings),
        "max_question_length": assistant.MAX_QUESTION_LENGTH,
        **extra,
    }, status_code=status_code, headers=_PRIVATE_HEADERS)


def _stale_form(request: Request, db: Session, patient_id: int, *, question: str = ""):
    if _is_htmx(request):
        # Меняем только сообщение: HTMX по умолчанию не показывает тело ответа 403.
        # Саму форму, черновик и переписку не заменяем и ничего не сохраняем.
        return templates.TemplateResponse(request, "partials/assistant_recovery.html", {
            "message": _STALE_FORM,
        }, headers={
            **_PRIVATE_HEADERS,
            "HX-Retarget": "#assistant-recovery",
            "HX-Reswap": "innerHTML show:#assistant-recovery:top",
        })
    return _render(
        request, db, patient_id, status_code=403,
        error="Форма устарела. Проверьте данные и попробуйте отправить её ещё раз.",
        question=question[:assistant.MAX_QUESTION_LENGTH],
    )


def _success(request: Request, db: Session, patient_id: int, **extra):
    if _is_htmx(request):
        return _render(request, db, patient_id, **extra)
    return RedirectResponse("/assistant#assistant-question", status_code=303, headers=_PRIVATE_HEADERS)


@router.get("", response_class=HTMLResponse)
def assistant_page(request: Request, user: User = Depends(_patient), db: Session = Depends(get_db)):
    return _render(request, db, user.id)


@router.post("", response_class=HTMLResponse)
def ask_assistant(
    request: Request,
    question: str = Form(""),
    csrf_token: str = Form(""),
    user: User = Depends(_patient),
    db: Session = Depends(get_db),
):
    patient_id = user.id
    if not _valid_token(request, csrf_token):
        return _stale_form(request, db, patient_id, question=question)

    question = question.strip()
    if not question:
        return _render(request, db, patient_id, error="Напишите вопрос.")
    if len(question) > assistant.MAX_QUESTION_LENGTH:
        return _render(request, db, patient_id, error=f"Сократите вопрос до {assistant.MAX_QUESTION_LENGTH} символов.")
    if assistant.is_urgent(question):
        return _render(request, db, patient_id, immediate_answer=(
            "Если это происходит сейчас, позвоните 112. Не ждите ответа в чате."
        ), urgent=True)
    if assistant.contains_personal_data(question):
        return _render(request, db, patient_id, question=question, error=(
            "Уберите из вопроса почту, номер телефона или документа и отправьте снова."
        ))
    if assistant.asks_for_individual_advice(question):
        return _render(request, db, patient_id, immediate_answer=_REFER_TO_DOCTOR)
    if not assistant.configured(settings):
        return _render(request, db, patient_id, error=_UNAVAILABLE, question=question)

    revision, history = assistant.start_exchange(db, patient_id)
    try:
        answer = assistant.make_answer(settings, question, history)
    except assistant.AssistantUnsafeAnswer:
        return _render(request, db, patient_id, question=question, immediate_answer=_REFER_TO_DOCTOR)
    except assistant.AssistantBusy:
        error = "Слишком много вопросов одновременно. Попробуйте чуть позже."
    except assistant.AssistantTimeout:
        error = (
            "Помощник не успел ответить. Вопрос остался в поле. Проверьте последние сообщения "
            "перед повторной отправкой. Если вопрос срочный, не ждите ответа в чате."
        )
    except assistant.AssistantFiltered:
        error = "Помощник не смог ответить на этот вопрос. Попробуйте спросить по-другому или обратитесь к врачу."
    except assistant.AssistantUnavailable:
        error = _UNAVAILABLE
    else:
        if assistant.save_exchange(db, patient_id, revision, history, question, answer):
            return _success(request, db, patient_id)
        return _render(
            request, db, patient_id, question=question,
            error="Переписка изменилась. Проверьте последние сообщения перед повторной отправкой.",
        )
    return _render(request, db, patient_id, error=error, question=question)


@router.post("/clear", response_class=HTMLResponse)
def clear_assistant_chat(
    request: Request,
    csrf_token: str = Form(""),
    user: User = Depends(_patient),
    db: Session = Depends(get_db),
):
    if not _valid_token(request, csrf_token):
        return _stale_form(request, db, user.id)
    assistant.clear_history(db, user.id)
    return _success(request, db, user.id, notice="Переписка удалена.")
