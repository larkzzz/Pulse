"""Вход, выход и регистрация пациента."""
import hmac
import secrets
from datetime import date, datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import RegistrationRequest, User
from app.services import sessions
from app.services.accounts import oms_is_valid, validate_profile
from app.services.help import phone_href
from app.services.invites import code_status, limited_code_status, normalize_code, transition_invite
from app.services.security import hash_password, verify_password
from app.web import htmx_redirect, templates

router = APIRouter()
ROLE_HOME = {"patient": "/", "doctor": "/doctor", "admin": "/admin"}


def _auth_context(**extra) -> dict:
    return {"form": {}, "errors": {}, **extra}


def _code_status(request: Request, db: Session, raw: str):
    if not raw.strip():
        return code_status(db, raw)
    session_id = request.session.get("invite_session_id")
    if not session_id:
        session_id = request.session["invite_session_id"] = secrets.token_urlsafe(24)
    scopes = [f"ip:{request.client.host}"] if request.client else []
    scopes.append(f"session:{session_id}")
    keys = tuple(
        hmac.digest(settings.secret_key.encode(), scope.encode(), "sha256").hex() for scope in scopes
    )
    return limited_code_status(db, raw, keys)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "auth/login.html", _auth_context())


@router.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    errors = {}
    if "@" not in email:
        errors["email"] = "Напишите почту, которую указывали при регистрации"
    if not password:
        errors["password"] = "Напишите пароль"
    user = db.scalar(select(User).where(User.email == email)) if not errors else None
    if not errors and not user:
        errors["email"] = "Не нашли такую почту. Проверьте её или зарегистрируйтесь"
    elif user and not verify_password(password, user.password_hash):
        errors["password"] = "Неверный пароль. Попробуйте ещё раз"
    elif user and user.status == "blocked":
        errors["email"] = "Учётная запись заблокирована. Позвоните в регистратуру"
    if errors:
        return templates.TemplateResponse(
            request, "partials/login_form.html", _auth_context(form={"email": email}, errors=errors)
        )
    sessions.start(db, request.session, user.id)
    if user.status in ("pending", "rejected"):
        return htmx_redirect("/pending")
    return htmx_redirect(ROLE_HOME[user.role])


@router.get("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    sessions.end(db, request.session)
    db.commit()
    return RedirectResponse("/login", status_code=303)


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request, code: str = "", db: Session = Depends(get_db)):
    ctx = _auth_context(mode="code", form={"code": normalize_code(code)})
    if code:
        _, ctx["doctor"], ctx["code_error"] = _code_status(request, db, code)
    return templates.TemplateResponse(request, "auth/register.html", ctx, headers={
        "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
    })


@router.get("/register/no-code", response_class=HTMLResponse)
def register_no_code_page(request: Request):
    return templates.TemplateResponse(request, "auth/register.html", _auth_context(mode="request"))


@router.post("/register/check-code", response_class=HTMLResponse)
def check_code_on_input(request: Request, code: str = Form(""), db: Session = Depends(get_db)):
    _, doctor, error = _code_status(request, db, code)
    return templates.TemplateResponse(
        request, "partials/code_status.html", {"doctor": doctor, "code_error": error if code.strip() else ""}
    )


@router.post("/register", response_class=HTMLResponse)
def register(
    request: Request,
    mode: str = Form("code"),
    code: str = Form(""),
    oms: str = Form(""),
    last_name: str = Form(""),
    first_name: str = Form(""),
    patronymic: str = Form(""),
    birth_date: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    password2: str = Form(""),
    consent: str = Form(""),
    db: Session = Depends(get_db),
):
    mode = "request" if mode == "request" else "code"
    form = {"code": normalize_code(code) or code.strip(), "oms": oms.strip(),
            "last_name": last_name.strip(), "first_name": first_name.strip(),
            "patronymic": patronymic.strip(), "birth_date": birth_date,
            "phone": phone.strip(), "email": email.strip().lower(), "consent": bool(consent)}
    taken_emails = set(db.scalars(select(User.email)).all())
    errors = validate_profile(form, password, password2, taken_emails)

    invite = doctor = None
    if mode == "code":
        invite, doctor, code_error = _code_status(request, db, code)
        if code_error:
            errors["code"] = code_error
    elif not oms_is_valid(form["oms"]):
        errors["oms"] = "В полисе ОМС 16 цифр. Они есть на лицевой стороне полиса"

    if errors:
        return templates.TemplateResponse(
            request, "partials/register_form.html",
            _auth_context(mode=mode, form=form, errors=errors, doctor=doctor),
        )

    user = User(
        email=form["email"], password_hash=hash_password(password), role="patient",
        status="pending" if mode == "request" else "active",
        last_name=form["last_name"], first_name=form["first_name"],
        patronymic=form["patronymic"], birth_date=date.fromisoformat(form["birth_date"]),
        phone=form["phone"], doctor=doctor,
    )
    db.add(user)
    if mode == "request":
        db.flush()
        db.add(RegistrationRequest(patient=user, oms=form["oms"], submitted_at=datetime.now()))
    else:
        db.flush()
        patient_hint = " ".join(
            x for x in (form["last_name"], form["first_name"], form["patronymic"]) if x
        )
        if not transition_invite(
            db, invite.id, "used", used_by_id=user.id, patient_hint=patient_hint[:200],
        ):
            db.rollback()
            _, doctor, error = code_status(db, code)
            return templates.TemplateResponse(
                request, "partials/register_form.html",
                _auth_context(
                    mode=mode, form=form, doctor=doctor,
                    errors={"code": error or "Код больше не действует. Попросите новый код в регистратуре"},
                ),
            )
    sessions.start(db, request.session, user.id)
    if mode == "request":
        return htmx_redirect("/pending")
    return htmx_redirect("/cabinet?welcome=1")


@router.get("/pending", response_class=HTMLResponse)
def pending_page(request: Request, db: Session = Depends(get_db)):
    user = sessions.session_user(db, request.session, statuses=("active", "pending", "rejected"))
    if user and user.role == "patient" and user.status == "active":
        return RedirectResponse("/", status_code=303)
    registration = user.registration_request if user else None
    return templates.TemplateResponse(
        request, "auth/pending.html", _auth_context(
            registration=registration,
            help_available=bool(user and user.role == "patient" and user.status in ("pending", "rejected")),
            registry_phone=settings.registry_phone,
            registry_href=phone_href(settings.registry_phone),
        ),
    )
