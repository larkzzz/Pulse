"""Главная пациента: ближайший приём и лекарства на сегодня."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app import mock_data
from app.services.intakes import day_summary, today_intakes
from app.web import templates

router = APIRouter()


def _home_context() -> dict:
    intakes = today_intakes(mock_data.SCHEDULE, mock_data.TAKEN_TODAY)
    return {
        "patient": mock_data.PATIENT,
        "today": mock_data.TODAY_DATE,
        "intakes": intakes,
        **day_summary(intakes),
    }


@router.get("/", response_class=HTMLResponse)
def patient_home(request: Request):
    return templates.TemplateResponse(
        request, "patient/home.html", {**_home_context(), "active_tab": "home"}
    )


@router.post("/intakes/{schedule_id}/{key}/take", response_class=HTMLResponse)
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
