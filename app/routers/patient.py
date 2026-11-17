"""Главная пациента: ближайший приём и лекарства на сегодня."""
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.access import require_roles
from app.database import get_db
from app.models import Intake, Medication, User
from app.services.intakes import day_summary, today_intakes, today_label
from app.web import templates

router = APIRouter()


def _home_context(db: Session, user: User) -> dict:
    today = date.today()
    medications = db.scalars(select(Medication).where(
        Medication.user_id == user.id,
        or_(Medication.until.is_(None), Medication.until >= today),
    )).all()
    schedule = [
        {"id": med.id, "drug": med.drug, "dose": med.dose, "meal": med.meal, "times": med.times}
        for med in medications
    ]
    taken = db.scalars(select(Intake).join(Medication).where(
        Medication.user_id == user.id, Intake.intake_date == today
    )).all()
    taken_today = {(row.medication_id, row.time): row.taken_at.strftime("%H:%M") for row in taken}
    intakes = today_intakes(schedule, taken_today)
    return {
        "patient": user,
        "today": today_label(today),
        "intakes": intakes,
        **day_summary(intakes),
    }


@router.get("/", response_class=HTMLResponse)
def patient_home(
    request: Request,
    user: User = Depends(require_roles("patient")),
    db: Session = Depends(get_db),
):
    return templates.TemplateResponse(
        request, "patient/home.html", {**_home_context(db, user), "active_tab": "home"}
    )


@router.post("/intakes/{schedule_id}/{key}/take", response_class=HTMLResponse)
def take_intake(
    request: Request,
    schedule_id: int,
    key: str,
    user: User = Depends(require_roles("patient")),
    db: Session = Depends(get_db),
):
    if len(key) != 4 or not key.isdigit():
        raise HTTPException(status_code=404)
    intake_time = f"{key[:2]}:{key[2:]}"
    try:
        datetime.strptime(intake_time, "%H:%M")
    except ValueError as exc:
        raise HTTPException(status_code=404) from exc
    entry = db.scalar(select(Medication).where(
        Medication.id == schedule_id, Medication.user_id == user.id
    ))
    if not entry or intake_time not in entry.times:
        raise HTTPException(status_code=404)

    today = date.today()
    taken = db.scalar(select(Intake).where(
        Intake.medication_id == entry.id,
        Intake.intake_date == today,
        Intake.time == intake_time,
    ))
    if not taken:
        db.add(Intake(
            medication=entry, intake_date=today, time=intake_time, taken_at=datetime.now()
        ))
        db.commit()
    return templates.TemplateResponse(
        request, "partials/today.html", {**_home_context(db, user), "just_taken": entry}
    )
