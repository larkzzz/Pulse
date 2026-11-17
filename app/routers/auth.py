"""Вход и регистрация.

В макете настоящей авторизации нет: проверяем только заполнение полей.
Регистрация пациента — по коду приглашения от врача; без кода — заявка на проверку.
"""
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from app import mock_data
from app.config import settings
from app.services.accounts import oms_is_valid, validate_profile
from app.services.invites import check_code, normalize_code
from app.web import htmx_redirect, templates

router = APIRouter()


def _auth_context(**extra) -> dict:
    return {"form": {}, "errors": {}, "registry_phone": settings.registry_phone, **extra}


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "auth/login.html", _auth_context())


@router.post("/login", response_class=HTMLResponse)
def login(request: Request, email: str = Form(""), password: str = Form("")):
    email = email.strip().lower()
    errors = {}
    if "@" not in email:
        errors["email"] = "Напишите почту, которую указывали при регистрации"
    if not password:
        errors["password"] = "Напишите пароль"
    user = next((u for u in mock_data.USERS if u["email"] == email), None)
    if not errors and not user:
        errors["email"] = "Не нашли такую почту. Проверьте её или зарегистрируйтесь"
    if errors:
        return templates.TemplateResponse(
            request, "partials/login_form.html", _auth_context(form={"email": email}, errors=errors)
        )
    if user["status"] == "pending":
        return htmx_redirect("/pending")
    return htmx_redirect(mock_data.ROLE_HOME[user["role"]])


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request, code: str = ""):
    """Регистрация по коду. Ссылка из QR-кода: /register?code=PLS-4827 — код уже вписан."""
    ctx = _auth_context(mode="code", form={"code": normalize_code(code)})
    if code:
        _, ctx["doctor"], ctx["code_error"] = check_code(code, mock_data.INVITE_CODES, mock_data.DOCTORS)
    return templates.TemplateResponse(request, "auth/register.html", ctx)


@router.get("/register/no-code", response_class=HTMLResponse)
def register_no_code_page(request: Request):
    return templates.TemplateResponse(request, "auth/register.html", _auth_context(mode="request"))


@router.post("/register/check-code", response_class=HTMLResponse)
def check_code_on_input(request: Request, code: str = Form("")):
    """Проверка кода сразу при вводе: показываем врача или ошибку."""
    _, doctor, error = check_code(code, mock_data.INVITE_CODES, mock_data.DOCTORS)
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
):
    mode = "request" if mode == "request" else "code"
    form = {"code": normalize_code(code) or code.strip(), "oms": oms.strip(),
            "last_name": last_name.strip(), "first_name": first_name.strip(),
            "patronymic": patronymic.strip(), "birth_date": birth_date,
            "phone": phone.strip(), "email": email.strip().lower(), "consent": bool(consent)}
    errors = validate_profile(form, password, password2, {u["email"] for u in mock_data.USERS})

    invite = doctor = None
    if mode == "code":
        invite, doctor, code_error = check_code(code, mock_data.INVITE_CODES, mock_data.DOCTORS)
        if code_error:
            errors["code"] = code_error
    elif not oms_is_valid(form["oms"]):
        errors["oms"] = "В полисе ОМС 16 цифр. Они есть на лицевой стороне полиса"

    if errors:
        return templates.TemplateResponse(
            request, "partials/register_form.html",
            _auth_context(mode=mode, form=form, errors=errors, doctor=doctor),
        )

    full_name = " ".join(x for x in (form["last_name"], form["first_name"], form["patronymic"]) if x)

    if mode == "request":
        mock_data.USERS.append({"email": form["email"], "role": "patient", "status": "pending"})
        mock_data.REQUESTS.append({
            "email": form["email"], "name": full_name, "birth_date": form["birth_date"],
            "phone": form["phone"], "oms": form["oms"], "submitted": "сегодня", "status": "pending",
        })
        return htmx_redirect("/pending")

    # Регистрация по коду: пациент сразу закреплён за врачом из кода.
    # В макете просто заменяем тестового пациента и очищаем его лекарства.
    invite["status"] = "used"
    invite["patient_hint"] = full_name
    mock_data.USERS.append({"email": form["email"], "role": "patient", "status": "active"})
    mock_data.PATIENT.update(
        last_name=form["last_name"], first_name=form["first_name"],
        patronymic=form["patronymic"], phone=form["phone"], email=form["email"],
        doctor=doctor["name"], doctor_role=doctor["role"],
    )
    mock_data.SCHEDULE.clear()
    mock_data.TAKEN_TODAY.clear()
    return htmx_redirect("/cabinet?welcome=1")


@router.get("/pending", response_class=HTMLResponse)
def pending_page(request: Request):
    """Заявка без кода ждёт проверки администратором."""
    return templates.TemplateResponse(request, "auth/pending.html", _auth_context())
