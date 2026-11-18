"""Экран экстренной помощи и поиск медпункта."""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access import AccessDenied, AuthenticationRequired
from app.config import settings
from app.database import get_db
from app.models import Fap, User
from app.services.help import coordinates, fap_coordinates, fap_map_link, is_demo_fap, nearest_fap, phone_href
from app.web import templates

router = APIRouter(prefix="/help")


def _help_patient(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    user = db.get(User, user_id) if user_id else None
    if not user or user.status not in ("active", "pending", "rejected"):
        raise AuthenticationRequired
    if user.role != "patient":
        raise AccessDenied
    return user


def _faps(db: Session) -> list[Fap]:
    return [
        fap for fap in db.scalars(select(Fap).order_by(Fap.village, Fap.name, Fap.id))
        if not is_demo_fap(fap)
    ]


def _fap_context(fap: Fap | None, message: str = "", is_nearest: bool = False) -> dict:
    return {
        "fap": fap,
        "fap_phone_href": phone_href(fap.phone) if fap else "",
        "fap_point": fap_coordinates(fap) if fap else None,
        "fap_map_link": fap_map_link(fap) if fap else "",
        "maps_enabled": bool(settings.yandex_maps_api_key),
        "fap_message": message,
        "is_nearest": is_nearest,
    }


@router.get("", response_class=HTMLResponse)
def help_page(
    request: Request,
    fap_id: str = "",
    user: User = Depends(_help_patient),
    db: Session = Depends(get_db),
):
    faps = _faps(db)
    selected = next((fap for fap in faps if str(fap.id) == fap_id), None)
    message = "Не нашли этот медпункт. Выберите другой из списка." if fap_id and not selected and faps else ""
    return templates.TemplateResponse(request, "patient/help.html", {
        **_fap_context(selected, message),
        "active_tab": "help",
        "active_patient": user.status == "active",
        "registry_phone": settings.registry_phone,
        "registry_href": phone_href(settings.registry_phone),
        "faps": faps,
        "has_mapped_faps": any(fap_coordinates(fap) for fap in faps),
        "maps_api_key": settings.yandex_maps_api_key,
    })


@router.post("/nearest", response_class=HTMLResponse)
def find_nearest_fap(
    request: Request,
    latitude: str = Form(""),
    longitude: str = Form(""),
    _: User = Depends(_help_patient),
    db: Session = Depends(get_db),
):
    try:
        point = coordinates(float(latitude), float(longitude))
    except ValueError:
        point = None
    fap = nearest_fap(_faps(db), *point) if point else None
    if not point:
        message = "Не удалось определить ваше местоположение. Выберите медпункт из списка."
    elif not fap:
        message = "Не удалось найти ближайший медпункт. Если список доступен, выберите медпункт вручную."
    else:
        message = ""
    return templates.TemplateResponse(
        request, "partials/help_fap.html", _fap_context(fap, message, is_nearest=True),
        headers={"Cache-Control": "no-store"},
    )
