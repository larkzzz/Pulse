"""Кабинет пациента: свои лекарства, данные и настройки напоминаний."""
from datetime import date, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access import require_roles
from app.database import get_db
from app.models import Medication, User
from app.services.intakes import today_label
from app.web import templates

router = APIRouter()
DRUGS = [
    "Аторвастатин", "Амлодипин", "Бисопролол", "Варфарин", "Лозартан",
    "Метформин", "Омепразол", "Эналаприл", "Ацетилсалициловая кислота",
]
MEAL_OPTIONS = ["до еды", "во время еды", "после еды", "не важно"]


def _cabinet_context(db: Session, user: User, **extra) -> dict:
    schedule = db.scalars(select(Medication).where(Medication.user_id == user.id)).all()
    return {
        "patient": user,
        "profile_form": {
            name: getattr(user, name)
            for name in ("last_name", "first_name", "patronymic", "phone", "email")
        },
        "today": today_label(),
        "schedule": sorted(schedule, key=lambda entry: entry.times[0] if entry.times else ""),
        "notifications": {
            "push": user.notification_push,
            "email": user.notification_email,
            "voice": user.notification_voice,
        },
        "drugs": DRUGS,
        "meal_options": MEAL_OPTIONS,
        **extra,
    }


@router.get("/cabinet", response_class=HTMLResponse)
def patient_cabinet(
    request: Request,
    welcome: int = 0,
    user: User = Depends(require_roles("patient")),
    db: Session = Depends(get_db),
):
    return templates.TemplateResponse(
        request, "patient/cabinet.html",
        {**_cabinet_context(db, user), "active_tab": "cabinet", "welcome": bool(welcome)},
    )


@router.post("/cabinet/medications", response_class=HTMLResponse)
def add_medication(
    request: Request,
    drug: str = Form(""),
    dose: str = Form(""),
    meal: str = Form("не важно"),
    time1: str = Form(""),
    time2: str = Form(""),
    time3: str = Form(""),
    until: str = Form(""),
    user: User = Depends(require_roles("patient")),
    db: Session = Depends(get_db),
):
    form = {"drug": drug.strip(), "dose": dose.strip(), "meal": meal,
            "time1": time1, "time2": time2, "time3": time3, "until": until}
    times = sorted({value for value in (time1, time2, time3) if value})
    errors = {}
    if not form["drug"]:
        errors["drug"] = "Напишите название лекарства"
    if not form["dose"]:
        errors["dose"] = "Напишите дозу, например «10 мг, 1 таблетка»"
    if not times:
        errors["time1"] = "Укажите хотя бы одно время приёма"
    for value in times:
        try:
            datetime.strptime(value, "%H:%M")
        except ValueError:
            errors["time1"] = "Проверьте время приёма"
    until_date = None
    if until:
        try:
            until_date = date.fromisoformat(until)
        except ValueError:
            errors["until"] = "Проверьте дату окончания приёма"

    added = None
    if not errors:
        added = Medication(
            patient=user, drug=form["drug"], dose=form["dose"],
            meal=meal if meal in MEAL_OPTIONS else "не важно",
            times=times, until=until_date,
        )
        db.add(added)
        db.commit()
        form = {}

    return templates.TemplateResponse(
        request, "partials/cabinet_meds.html",
        _cabinet_context(db, user, form=form, errors=errors, added=added),
    )


@router.delete("/cabinet/medications/{schedule_id}", response_class=HTMLResponse)
def delete_medication(
    request: Request,
    schedule_id: int,
    user: User = Depends(require_roles("patient")),
    db: Session = Depends(get_db),
):
    entry = db.scalar(select(Medication).where(
        Medication.id == schedule_id, Medication.user_id == user.id
    ))
    if not entry:
        raise HTTPException(status_code=404)
    db.delete(entry)
    db.commit()
    return templates.TemplateResponse(
        request, "partials/cabinet_meds.html", _cabinet_context(db, user, form={}, errors={})
    )


@router.post("/cabinet/profile", response_class=HTMLResponse)
def save_profile(
    request: Request,
    last_name: str = Form(""),
    first_name: str = Form(""),
    patronymic: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    user: User = Depends(require_roles("patient")),
    db: Session = Depends(get_db),
):
    form = {"last_name": last_name.strip(), "first_name": first_name.strip(),
            "patronymic": patronymic.strip(), "phone": phone.strip(),
            "email": email.strip().lower()}
    errors = {}
    if not form["last_name"]:
        errors["last_name"] = "Напишите фамилию"
    if not form["first_name"]:
        errors["first_name"] = "Напишите имя"
    if sum(char.isdigit() for char in form["phone"]) < 10:
        errors["phone"] = "Напишите номер телефона полностью"
    if "@" not in form["email"]:
        errors["email"] = "Нужна почта, на неё придут напоминания"
    elif db.scalar(select(User.id).where(User.email == form["email"], User.id != user.id)):
        errors["email"] = "Такая почта уже зарегистрирована"
    if not errors:
        for name, value in form.items():
            setattr(user, name, value)
        db.commit()
    return templates.TemplateResponse(
        request, "partials/cabinet_profile.html",
        _cabinet_context(db, user, form=form, errors=errors, saved=not errors),
    )


@router.post("/cabinet/notifications", response_class=HTMLResponse)
def save_notifications(
    request: Request,
    push: str = Form(""),
    email: str = Form(""),
    voice: str = Form(""),
    user: User = Depends(require_roles("patient")),
    db: Session = Depends(get_db),
):
    user.notification_push = bool(push)
    user.notification_email = bool(email)
    user.notification_voice = bool(voice)
    db.commit()
    return templates.TemplateResponse(
        request, "partials/cabinet_notifications.html", _cabinet_context(db, user, saved=True)
    )
