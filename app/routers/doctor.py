"""Кабинет врача: закреплённые пациенты, приёмы и приглашения."""
import hmac
import secrets
from base64 import b64encode
from datetime import date
from urllib.parse import urlencode, urlsplit

import qrcode
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from qrcode.exceptions import DataOverflowError
from qrcode.image.svg import SvgPathFillImage
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access import AccessDenied, require_roles
from app.config import settings
from app.database import get_db
from app.models import Doctor, InviteCode, User
from app.routers.doctor_chat import PRIVATE, chat_context, composer_context
from app.services import doctor_chat, invites
from app.services.intakes import patient_day
from app.web import templates

router = APIRouter(prefix="/doctor")
_doctor = require_roles("doctor")


def _profile(account: User = Depends(_doctor), db: Session = Depends(get_db)) -> Doctor:
    doctor = db.scalar(select(Doctor).where(Doctor.user_id == account.id))
    if doctor is None:
        raise AccessDenied
    return doctor


def _patients_page(request: Request, db: Session, doctor: Doctor, patient_id: int | None = None):
    patients = doctor_chat.assigned_patients(db, doctor.id)
    if patient_id is None:
        patient = patients[0] if patients else None
    else:
        patient = next((item for item in patients if item.id == patient_id), None)
        if patient is None:
            raise HTTPException(404, "Пациент не найден")
    context = {
        "doctor": doctor, "active_section": "patients", "patients": patients, "patient": patient,
    }
    if patient is not None:
        context.update(patient_day(db, patient.id, date.today()))
        context.update(chat_context(db, patient.id, doctor.account))
        context.update(composer_context(request, patient.id, doctor.id))
    return templates.TemplateResponse(request, "doctor/patients.html", context, headers=PRIVATE)


@router.get("", response_class=HTMLResponse)
def doctor_home(request: Request, db: Session = Depends(get_db), doctor: Doctor = Depends(_profile)):
    return _patients_page(request, db, doctor)


@router.get("/patients/{patient_id}", response_class=HTMLResponse)
def doctor_patient(
    request: Request, patient_id: int, db: Session = Depends(get_db), doctor: Doctor = Depends(_profile),
):
    return _patients_page(request, db, doctor, patient_id)


def _invites_page(request: Request, db: Session, doctor: Doctor, **extra):
    csrf = request.session.get("doctor_invite_csrf")
    if not csrf:
        csrf = request.session["doctor_invite_csrf"] = secrets.token_urlsafe(24)
    return templates.TemplateResponse(request, "doctor/invites.html", {
        "doctor": doctor, "active_section": "invites", "csrf_token": csrf,
        "invites": invites.list_invites(db, doctor.id), "statuses": invites.INVITE_STATUSES,
        "notice": request.session.pop("doctor_invite_notice", ""), **extra,
    }, headers=PRIVATE)


def _require_csrf(request: Request, token: str) -> None:
    expected = request.session.get("doctor_invite_csrf")
    if not expected or not token.isascii() or not hmac.compare_digest(expected, token):
        raise HTTPException(403, "Форма устарела. Откройте страницу заново и повторите действие.")


def _own_invite(db: Session, doctor: Doctor, invite_id: int) -> InviteCode:
    invite = db.scalar(select(InviteCode).where(
        InviteCode.id == invite_id, InviteCode.doctor_id == doctor.id,
    ))
    if invite is None:
        raise HTTPException(404, "Код не найден")
    return invite


@router.get("/invites", response_class=HTMLResponse)
def invite_list(request: Request, db: Session = Depends(get_db), doctor: Doctor = Depends(_profile)):
    return _invites_page(request, db, doctor)


@router.post("/invites", response_class=HTMLResponse)
def invite_create(
    request: Request, patient_hint: str = Form(""), csrf_token: str = Form(""),
    db: Session = Depends(get_db), doctor: Doctor = Depends(_profile),
):
    _require_csrf(request, csrf_token)
    try:
        invite = invites.issue_invite(db, doctor.id, patient_hint)
    except invites.InviteError as exc:
        return _invites_page(request, db, doctor, error=str(exc), patient_hint=patient_hint[:200])
    return RedirectResponse(f"/doctor/invites/{invite.id}/print", status_code=303, headers=PRIVATE)


@router.post("/invites/{invite_id}/revoke", response_class=HTMLResponse)
def invite_revoke(
    request: Request, invite_id: int, csrf_token: str = Form(""),
    db: Session = Depends(get_db), doctor: Doctor = Depends(_profile),
):
    _require_csrf(request, csrf_token)
    invite = _own_invite(db, doctor, invite_id)
    try:
        invites.revoke_invite(db, invite)
    except invites.InviteError as exc:
        return _invites_page(request, db, doctor, error=str(exc))
    request.session["doctor_invite_notice"] = f"Код {invite.code} отменён"
    return RedirectResponse("/doctor/invites", status_code=303, headers=PRIVATE)


@router.get("/invites/{invite_id}/print", response_class=HTMLResponse)
def invite_print(
    request: Request, invite_id: int, db: Session = Depends(get_db), doctor: Doctor = Depends(_profile),
):
    invite = _own_invite(db, doctor, invite_id)
    if invites.invite_status(invite, date.today()) != "new":
        return _invites_page(request, db, doctor, error="Распечатать можно только действующий код.")

    base_url = settings.base_url.rstrip("/")
    try:
        address = urlsplit(base_url)
        if (address.scheme not in ("http", "https") or not address.hostname
                or address.username or address.password or address.query or address.fragment):
            raise ValueError
        registration_url = f"{base_url}/register?{urlencode({'code': invite.code})}"
        image = qrcode.make(registration_url, image_factory=SvgPathFillImage)
    except (ValueError, DataOverflowError):
        return _invites_page(request, db, doctor, error="Не удалось создать QR-код. Проверьте адрес сайта в настройках.")
    return templates.TemplateResponse(request, "doctor/invite_print.html", {
        "doctor": doctor, "invite": invite, "base_url": base_url, "registration_url": registration_url,
        "qr_svg": b64encode(image.to_string()).decode("ascii"),
    }, headers=PRIVATE)
