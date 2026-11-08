from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
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
    """Отметка «Принял». Отдаём обновлённый блок «сегодня»."""
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
def patient_cabinet(request: Request):
    return templates.TemplateResponse(
        request, "patient/cabinet.html", {**_cabinet_context(), "active_tab": "cabinet"}
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
