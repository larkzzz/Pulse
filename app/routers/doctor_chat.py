"""Чат с врачом: HTML-страницы и обновления по WebSocket для HTMX."""
import asyncio
import hmac
import re
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.access import AccessDenied, AuthenticationRequired, require_roles
from app.config import settings
from app.database import SessionLocal, get_db
from app.models import User
from app.services import chat_events, doctor_chat as chat, sessions
from app.web import templates

router = APIRouter()
PRIVATE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
_CHECK_INTERVAL = 5.0
_participant = require_roles("doctor", "patient")


def _not_found() -> HTTPException:
    return HTTPException(404, "Переписка не найдена")


def chat_context(db: Session, patient_id: int, viewer: User, before: int | None = None) -> dict:
    messages, older = chat.history(db, patient_id, before)
    return {
        "messages": messages, "older": older,
        "patient_id": patient_id, "viewer_id": viewer.id, "viewer_role": viewer.role,
    }


def _csrf(request: Request) -> str:
    login_id = request.session["login_id"]
    return hmac.digest(settings.secret_key.encode(), f"doctor-chat:{login_id}".encode(), "sha256").hex()


def composer_context(request: Request, patient_id: int, doctor_id: int) -> dict:
    return {
        "message_action": f"/doctor-chat/{patient_id}/messages", "channel_doctor_id": doctor_id,
        "csrf_token": _csrf(request), "max_message_length": chat.MAX_MESSAGE_LENGTH,
        "submission_id": uuid4().hex,
    }


def _draft_only(request: Request, message: str, error: str, *, login_required: bool = False) -> HTMLResponse:
    return templates.TemplateResponse(request, "partials/doctor_chat_composer.html", {
        "draft": message[:chat.MAX_MESSAGE_LENGTH], "error": error,
        "read_only": True, "login_required": login_required,
    }, headers=PRIVATE)


@router.get("/doctor-chat", response_class=HTMLResponse)
def patient_chat(
    request: Request, patient: User = Depends(require_roles("patient")),
    db: Session = Depends(get_db),
):
    doctor = chat.active_doctor(db, patient)
    return templates.TemplateResponse(request, "patient/doctor_chat.html", {
        "patient": patient, "active_tab": "doctor", "doctor": doctor,
        **chat_context(db, patient.id, patient),
        **(composer_context(request, patient.id, doctor.id) if doctor else {}),
    }, headers=PRIVATE)


@router.get("/doctor-chat/history/{patient_id}", response_class=HTMLResponse)
def chat_history(
    request: Request, patient_id: int, before: int | None = None,
    viewer: User = Depends(_participant),
    db: Session = Depends(get_db),
):
    if not _valid_id(patient_id) or (before is not None and not _valid_id(before)):
        raise _not_found()
    allowed = viewer.id == patient_id if viewer.role == "patient" else chat.can_chat(db, viewer, patient_id)
    if not allowed:
        raise _not_found()
    back = "/doctor-chat" if viewer.role == "patient" else f"/doctor/patients/{patient_id}"
    return templates.TemplateResponse(request, "doctor/chat_history.html", {
        "back": back, "active_tab": "doctor", **chat_context(db, patient_id, viewer, before),
    }, headers=PRIVATE)


@router.post("/doctor-chat/{patient_id}/messages", response_class=HTMLResponse)
def send_message(
    request: Request, patient_id: int, doctor_id: int = Form(0), submission_id: str = Form(""),
    message: str = Form(""), csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    htmx = request.headers.get("HX-Request") == "true"
    viewer = sessions.session_user(db, request.session)
    if viewer is None:
        if htmx:
            # Не уводим со страницы входом: в форме может быть неотправленный текст.
            return _draft_only(
                request, message, "Сеанс завершился. Сообщение не отправлено. Скопируйте черновик и войдите снова.",
                login_required=True,
            )
        raise AuthenticationRequired
    if viewer.role not in ("doctor", "patient"):
        raise AccessDenied
    if not csrf_token.isascii() or not hmac.compare_digest(_csrf(request), csrf_token):
        raise HTTPException(403, "Форма устарела. Откройте страницу заново.")
    if not _valid_id(patient_id) or not _valid_id(doctor_id):
        raise _not_found()

    context = composer_context(request, patient_id, doctor_id)
    try:
        chat.save_message(db, request.session, patient_id, doctor_id, submission_id, message)
    except chat.ChatUnavailable as exc:
        if not htmx:
            raise _not_found() from exc
        return _draft_only(request, message, str(exc))
    except chat.MessageError as exc:
        context.update(error=str(exc), draft=message[:chat.MAX_MESSAGE_LENGTH])
        if not isinstance(exc, chat.SubmissionConflict) and re.fullmatch(r"[0-9a-f]{32}", submission_id):
            context["submission_id"] = submission_id
    else:
        if not htmx:
            back = "/doctor-chat" if viewer.role == "patient" else f"/doctor/patients/{patient_id}"
            return RedirectResponse(back, status_code=303, headers=PRIVATE)
        context["sent"] = True
    return templates.TemplateResponse(request, "partials/doctor_chat_composer.html", context, headers=PRIVATE)


def _valid_id(value: int) -> bool:
    return 0 < value <= 2_147_483_647


def _same_origin(websocket: WebSocket) -> bool:
    # WebSocket отправляет cookie и при запросе со сторонней страницы.
    # За TLS-прокси Uvicorn должен получать доверенный X-Forwarded-Proto.
    scheme = "https" if websocket.url.scheme == "wss" else "http"
    return websocket.headers.getlist("origin") == [f"{scheme}://{websocket.url.netloc}"]


def _room(cookie: dict, patient_id: int, doctor_id: int | None) -> chat.Room | None:
    with SessionLocal() as db:
        return chat.room(db, cookie, patient_id, doctor_id)


def _room_version(cookie: dict, patient_id: int, doctor_id: int):
    with SessionLocal() as db:
        return chat.room_version(db, cookie, patient_id, doctor_id)


def _frame(patient_id: int, room: chat.Room | None, *, connected: bool = False) -> str:
    return templates.get_template("partials/doctor_chat_update.html").render(
        patient_id=patient_id, room=room, connected=connected,
        messages=room.messages if room else [], older=room.older if room else None,
        viewer_id=room.viewer_id if room else None,
    )


@router.websocket("/doctor-chat/ws/{patient_id}")
async def chat_socket(websocket: WebSocket, patient_id: int, doctor_id: int | None = None):
    if not _same_origin(websocket) or not _valid_id(patient_id) or (doctor_id is not None and not _valid_id(doctor_id)):
        await websocket.close(code=1008)
        return
    cookie = dict(websocket.session)
    with chat_events.subscribe(patient_id) as signal:
        room = await run_in_threadpool(_room, cookie, patient_id, doctor_id)
        if room is None:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        doctor_id = room.doctor_id
        reader = asyncio.create_task(websocket.receive())
        wake = asyncio.create_task(signal.wait())
        try:
            await websocket.send_text(_frame(patient_id, room, connected=True))
            while True:
                done, _ = await asyncio.wait({reader, wake}, timeout=_CHECK_INTERVAL)
                if reader in done:
                    if reader.result()["type"] != "websocket.disconnect":
                        await websocket.close(code=1008)  # Отправка сообщений идёт через HTTP-форму.
                    return
                if wake in done:
                    signal.clear()
                    wake = asyncio.create_task(signal.wait())
                    updated = await run_in_threadpool(_room, cookie, patient_id, doctor_id)
                else:
                    version = await run_in_threadpool(_room_version, cookie, patient_id, doctor_id)
                    if version == room.version:
                        continue
                    updated = None if version is None else await run_in_threadpool(_room, cookie, patient_id, doctor_id)
                if updated is None:
                    await websocket.send_text(_frame(patient_id, None))
                    await websocket.close(code=1008)
                    return
                if updated.version != room.version:
                    await websocket.send_text(_frame(patient_id, updated))
                room = updated
        except WebSocketDisconnect:
            pass
        finally:
            reader.cancel()
            wake.cancel()
            await asyncio.gather(reader, wake, return_exceptions=True)
