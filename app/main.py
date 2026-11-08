from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import mock_data

BASE_DIR = Path(__file__).parent

app = FastAPI(title="Пульс")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def _find_intake(intake_id: int) -> dict | None:
    return next((i for i in mock_data.INTAKES if i["id"] == intake_id), None)


def _next_intake() -> dict | None:
    return next((i for i in mock_data.INTAKES if i["status"] == "next"), None)


def _home_context() -> dict:
    intakes = mock_data.INTAKES
    return {
        "patient": mock_data.PATIENT,
        "today": mock_data.TODAY_DATE,
        "intakes": intakes,
        "next_intake": _next_intake(),
        "taken_count": sum(1 for i in intakes if i["status"] == "taken"),
        "total_count": len(intakes),
    }


@app.get("/", response_class=HTMLResponse)
def patient_home(request: Request):
    return templates.TemplateResponse(
        request, "patient/home.html", {**_home_context(), "active_tab": "home"}
    )


@app.post("/intakes/{intake_id}/take", response_class=HTMLResponse)
def take_intake(request: Request, intake_id: int):
    """Отметка «Принял». Для макета меняем данные в памяти и отдаём обновлённый блок."""
    intake = _find_intake(intake_id)
    if intake and intake["status"] == "next":
        intake["status"] = "taken"
        intake["taken_at"] = intake["time"]
        later = next((i for i in mock_data.INTAKES if i["status"] == "later"), None)
        if later:
            later["status"] = "next"
    return templates.TemplateResponse(
        request, "partials/today.html", {**_home_context(), "just_taken": intake}
    )
