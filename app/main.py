from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import mock_data

BASE_DIR = Path(__file__).parent

app = FastAPI(title="Пульс")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


# ---------- Приёмы на сегодня ----------

def _today_intakes() -> list[dict]:
    """Список приёмов на сегодня по расписанию, по времени.
    Первый непринятый приём — «ближайший», остальные — «позже»."""
    items = []
    for entry in mock_data.SCHEDULE:
        for time in entry["times"]:
            taken_at = mock_data.TAKEN_TODAY.get((entry["id"], time))
            items.append({
                "schedule_id": entry["id"],
                "time": time,
                "key": time.replace(":", ""),
                "drug": entry["drug"],
                "dose": entry["dose"],
                "meal": entry["meal"],
                "status": "taken" if taken_at else "later",
                "taken_at": taken_at,
            })
    items.sort(key=lambda i: i["time"])
    for item in items:
        if item["status"] != "taken":
            item["status"] = "next"
            break
    return items


def _home_context() -> dict:
    intakes = _today_intakes()
    taken = sum(1 for i in intakes if i["status"] == "taken")
    return {
        "patient": mock_data.PATIENT,
        "today": mock_data.TODAY_DATE,
        "intakes": intakes,
        "next_intake": next((i for i in intakes if i["status"] == "next"), None),
        "taken_count": taken,
        "total_count": len(intakes),
        "progress_pct": round(taken / len(intakes) * 100) if intakes else 0,
    }


@app.get("/", response_class=HTMLResponse)
def patient_home(request: Request):
    return templates.TemplateResponse(
        request, "patient/home.html", {**_home_context(), "active_tab": "home"}
    )


@app.post("/intakes/{schedule_id}/{key}/take", response_class=HTMLResponse)
def take_intake(request: Request, schedule_id: int, key: str):
    """Отметка «Лекарство принято». Отдаём обновлённый блок «сегодня»."""
    time = f"{key[:2]}:{key[2:]}"
    entry = next((e for e in mock_data.SCHEDULE if e["id"] == schedule_id), None)
    just_taken = None
    if entry and time in entry["times"]:
        mock_data.TAKEN_TODAY.setdefault((schedule_id, time), time)
        just_taken = entry
    return templates.TemplateResponse(
        request, "partials/today.html", {**_home_context(), "just_taken": just_taken}
    )


# ---------- Кабинет пациента ----------

def _cabinet_context(**extra) -> dict:
    return {
        "patient": mock_data.PATIENT,
        "today": mock_data.TODAY_DATE,
        "schedule": sorted(mock_data.SCHEDULE, key=lambda e: e["times"][0]),
        "notifications": mock_data.NOTIFICATIONS,
        "drugs": mock_data.DRUGS,
        "meal_options": mock_data.MEAL_OPTIONS,
        **extra,
    }


@app.get("/cabinet", response_class=HTMLResponse)
def patient_cabinet(request: Request, welcome: int = 0):
    return templates.TemplateResponse(
        request, "patient/cabinet.html",
        {**_cabinet_context(), "active_tab": "cabinet", "welcome": bool(welcome)},
    )


@app.post("/cabinet/medications", response_class=HTMLResponse)
def add_medication(
    request: Request,
    drug: str = Form(""),
    dose: str = Form(""),
    meal: str = Form("не важно"),
    time1: str = Form(""),
    time2: str = Form(""),
    time3: str = Form(""),
    until: str = Form(""),
):
    """Добавить лекарство в расписание. Отдаём форму и обновлённый список."""
    form = {"drug": drug.strip(), "dose": dose.strip(), "meal": meal,
            "time1": time1, "time2": time2, "time3": time3, "until": until.strip()}
    times = sorted({t for t in (time1, time2, time3) if t})
    errors = {}
    if not form["drug"]:
        errors["drug"] = "Напишите название лекарства"
    if not form["dose"]:
        errors["dose"] = "Напишите дозу, например «10 мг, 1 таблетка»"
    if not times:
        errors["time1"] = "Укажите хотя бы одно время приёма"

    added = None
    if not errors:
        added = {
            "id": max((e["id"] for e in mock_data.SCHEDULE), default=0) + 1,
            "drug": form["drug"], "dose": form["dose"],
            "meal": meal if meal in mock_data.MEAL_OPTIONS else "не важно",
            "times": times, "until": form["until"],
        }
        mock_data.SCHEDULE.append(added)
        form = {}

    return templates.TemplateResponse(
        request, "partials/cabinet_meds.html",
        _cabinet_context(form=form, errors=errors, added=added),
    )


@app.delete("/cabinet/medications/{schedule_id}", response_class=HTMLResponse)
def delete_medication(request: Request, schedule_id: int):
    mock_data.SCHEDULE[:] = [e for e in mock_data.SCHEDULE if e["id"] != schedule_id]
    for key in [k for k in mock_data.TAKEN_TODAY if k[0] == schedule_id]:
        del mock_data.TAKEN_TODAY[key]
    return templates.TemplateResponse(
        request, "partials/cabinet_meds.html", _cabinet_context(form={}, errors={})
    )


@app.post("/cabinet/profile", response_class=HTMLResponse)
def save_profile(
    request: Request,
    last_name: str = Form(""),
    first_name: str = Form(""),
    patronymic: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
):
    errors = {}
    if not first_name.strip():
        errors["first_name"] = "Напишите имя"
    if "@" not in email:
        errors["email"] = "Нужна почта, на неё придут напоминания"
    if not errors:
        mock_data.PATIENT.update(
            last_name=last_name.strip(), first_name=first_name.strip(),
            patronymic=patronymic.strip(), phone=phone.strip(), email=email.strip(),
        )
    form = {"last_name": last_name, "first_name": first_name,
            "patronymic": patronymic, "phone": phone, "email": email}
    return templates.TemplateResponse(
        request, "partials/cabinet_profile.html",
        _cabinet_context(form=form, errors=errors, saved=not errors),
    )


@app.post("/cabinet/notifications", response_class=HTMLResponse)
def save_notifications(
    request: Request,
    push: str = Form(""),
    email: str = Form(""),
    voice: str = Form(""),
):
    mock_data.NOTIFICATIONS.update(push=bool(push), email=bool(email), voice=bool(voice))
    return templates.TemplateResponse(
        request, "partials/cabinet_notifications.html", _cabinet_context(saved=True)
    )


# ---------- Вход и регистрация ----------
# В макете настоящей авторизации нет: проверяем только заполнение полей.
# Регистрация пациента — по коду приглашения от врача; без кода — заявка на проверку.

def _redirect(url: str) -> Response:
    """HTMX сам перейдёт на страницу, увидев заголовок HX-Redirect."""
    return Response(status_code=204, headers={"HX-Redirect": url})


def _normalize_code(raw: str) -> str:
    """«pls 4827», «PLS4827», «4827» -> «PLS-4827»."""
    digits = "".join(c for c in raw if c.isdigit())
    return f"PLS-{digits}" if digits else ""


def _check_code(raw: str) -> tuple[dict | None, dict | None, str]:
    """Возвращает (код, врач, текст ошибки)."""
    code = _normalize_code(raw)
    if not code:
        return None, None, "Введите код с листка, который вам дали в больнице"
    invite = next((c for c in mock_data.INVITE_CODES if c["code"] == code), None)
    if not invite:
        return None, None, "Код не найден. Проверьте буквы и цифры или позвоните в регистратуру"
    if invite["status"] == "used":
        return None, None, "По этому коду уже зарегистрировались. Если это были не вы, позвоните в регистратуру"
    if invite["status"] in ("expired", "revoked"):
        return None, None, "Срок действия кода закончился. Попросите новый код у врача или в регистратуре"
    doctor = next(d for d in mock_data.DOCTORS if d["id"] == invite["doctor_id"])
    return invite, doctor, ""


def _auth_context(**extra) -> dict:
    return {"form": {}, "errors": {}, "registry_phone": mock_data.REGISTRY_PHONE, **extra}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "auth/login.html", _auth_context())


@app.post("/login", response_class=HTMLResponse)
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
        return _redirect("/pending")
    return _redirect(mock_data.ROLE_HOME[user["role"]])


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request, code: str = ""):
    """Регистрация по коду. Ссылка из QR-кода: /register?code=PLS-4827 — код уже вписан."""
    ctx = _auth_context(mode="code", form={"code": _normalize_code(code)})
    if code:
        _, ctx["doctor"], ctx["code_error"] = _check_code(code)
    return templates.TemplateResponse(request, "auth/register.html", ctx)


@app.get("/register/no-code", response_class=HTMLResponse)
def register_no_code_page(request: Request):
    return templates.TemplateResponse(request, "auth/register.html", _auth_context(mode="request"))


@app.post("/register/check-code", response_class=HTMLResponse)
def check_code(request: Request, code: str = Form("")):
    """Проверка кода сразу при вводе: показываем врача или ошибку."""
    _, doctor, error = _check_code(code)
    return templates.TemplateResponse(
        request, "partials/code_status.html", {"doctor": doctor, "code_error": error if code.strip() else ""}
    )


def _validate_profile(form: dict, password: str, password2: str) -> dict:
    errors = {}
    if not form["last_name"]:
        errors["last_name"] = "Напишите фамилию"
    if not form["first_name"]:
        errors["first_name"] = "Напишите имя"
    if not form["birth_date"]:
        errors["birth_date"] = "Укажите дату рождения"
    if sum(c.isdigit() for c in form["phone"]) < 10:
        errors["phone"] = "Напишите номер телефона полностью, например +7 917 555-12-34"
    if "@" not in form["email"]:
        errors["email"] = "Нужна почта, на неё будут приходить напоминания"
    elif any(u["email"] == form["email"] for u in mock_data.USERS):
        errors["email"] = "Такая почта уже зарегистрирована. Попробуйте войти"
    if len(password) < 8:
        errors["password"] = "Пароль должен быть не короче 8 знаков"
    elif password != password2:
        errors["password2"] = "Пароли не совпадают"
    if not form["consent"]:
        errors["consent"] = "Без согласия мы не можем хранить ваши данные"
    return errors


@app.post("/register", response_class=HTMLResponse)
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
    form = {"code": _normalize_code(code) or code.strip(), "oms": oms.strip(),
            "last_name": last_name.strip(), "first_name": first_name.strip(),
            "patronymic": patronymic.strip(), "birth_date": birth_date,
            "phone": phone.strip(), "email": email.strip().lower(), "consent": bool(consent)}
    errors = _validate_profile(form, password, password2)

    invite = doctor = None
    if mode == "code":
        invite, doctor, code_error = _check_code(code)
        if code_error:
            errors["code"] = code_error
    elif sum(c.isdigit() for c in form["oms"]) != 16:
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
        return _redirect("/pending")

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
    return _redirect("/cabinet?welcome=1")


@app.get("/pending", response_class=HTMLResponse)
def pending_page(request: Request):
    """Заявка без кода ждёт проверки администратором."""
    return templates.TemplateResponse(request, "auth/pending.html", _auth_context())
