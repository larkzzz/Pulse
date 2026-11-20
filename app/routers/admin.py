"""Кабинет администратора: доступ к пациентам, врачам и справочникам."""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.access import require_roles
from app.database import get_db
from app.models import User
from app.services import admin as admin_service
from app.services.admin import AdminError
from app.services.invites import INVITE_STATUSES, list_invites
from app.web import templates

router = APIRouter(prefix="/admin")
admin_required = require_roles("admin")

REQUEST_STATUSES = {
    "pending": "На проверке",
    "approved": "Подтверждена",
    "rejected": "Отклонена",
}


def _number(value: str) -> int:
    number = int(value) if len(value) <= 10 and value.isascii() and value.isdecimal() else 0
    return number if number <= 2_147_483_647 else 0


def _flash(request: Request, message: str = "", error: str = "") -> None:
    request.session["admin_notice"] = message
    request.session["admin_error"] = error


def _render(request: Request, db: Session, section: str, **extra):
    context = {
        "active_section": section,
        "notice": request.session.pop("admin_notice", ""),
        "error": request.session.pop("admin_error", ""),
        "form": {},
        **extra,
    }
    if section == "invites":
        context.update(
            invites=list_invites(db),
            doctors=admin_service.active_doctors(db),
            invite_statuses=INVITE_STATUSES,
        )
    elif section == "requests":
        context.update(
            requests=admin_service.list_requests(db),
            doctors=admin_service.active_doctors(db),
            request_statuses=REQUEST_STATUSES,
        )
    elif section == "patients":
        context.update(
            patients=admin_service.list_patients(db),
            doctors=admin_service.active_doctors(db),
            doctor_rows=admin_service.doctor_rows(db),
        )
    elif section == "doctors":
        context["doctor_rows"] = admin_service.doctor_rows(db)
    elif section == "references":
        if extra.get("reference_kind") == "drugs":
            context["drugs"] = admin_service.list_drugs(db)
        else:
            context["faps"] = admin_service.list_faps(db)
    template = "admin/references.html" if section == "references" else f"admin/{section}.html"
    return templates.TemplateResponse(request, template, context)


def _redirect(section: str) -> RedirectResponse:
    urls = {
        "faps": "/admin/references/faps",
        "drugs": "/admin/references/drugs",
    }
    return RedirectResponse(urls.get(section, f"/admin/{section}"), status_code=303)


@router.get("", response_class=HTMLResponse)
def admin_home(_: User = Depends(admin_required)):
    return RedirectResponse("/admin/requests", status_code=303)


@router.get("/invites", response_class=HTMLResponse)
def invites_page(
    request: Request,
    _: User = Depends(admin_required),
    db: Session = Depends(get_db),
):
    return _render(request, db, "invites")


@router.post("/invites", response_class=HTMLResponse)
def invite_create(
    request: Request,
    doctor_id: str = Form(""),
    patient_hint: str = Form(""),
    _: User = Depends(admin_required),
    db: Session = Depends(get_db),
):
    try:
        invite = admin_service.create_invite(db, _number(doctor_id), patient_hint)
    except AdminError as exc:
        return _render(
            request, db, "invites", error=str(exc),
            form={"doctor_id": doctor_id, "patient_hint": patient_hint},
        )
    _flash(request, f"Создан код {invite.code}. Срок действия — 30 дней")
    return _redirect("invites")


@router.post("/invites/{invite_id}/revoke")
def invite_revoke(
    request: Request,
    invite_id: int,
    _: User = Depends(admin_required),
    db: Session = Depends(get_db),
):
    try:
        invite = admin_service.revoke_invite(db, invite_id)
    except AdminError as exc:
        _flash(request, error=str(exc))
    else:
        _flash(request, f"Код {invite.code} отменён")
    return _redirect("invites")


@router.get("/requests", response_class=HTMLResponse)
def requests_page(
    request: Request,
    _: User = Depends(admin_required),
    db: Session = Depends(get_db),
):
    return _render(request, db, "requests")


@router.post("/requests/{request_id}/review")
def request_review(
    request: Request,
    request_id: int,
    decision: str = Form(""),
    doctor_id: str = Form(""),
    rejection_reason: str = Form(""),
    admin: User = Depends(admin_required),
    db: Session = Depends(get_db),
):
    try:
        registration = admin_service.review_request(
            db, request_id, admin, decision, _number(doctor_id), rejection_reason
        )
    except AdminError as exc:
        _flash(request, error=str(exc))
    else:
        message = (
            f"Заявка подтверждена. Врач: {registration.patient.doctor.name}"
            if decision == "approve" else "Заявка отклонена"
        )
        _flash(request, message)
    return _redirect("requests")


@router.get("/patients", response_class=HTMLResponse)
def patients_page(
    request: Request,
    _: User = Depends(admin_required),
    db: Session = Depends(get_db),
):
    return _render(request, db, "patients")


@router.post("/patients/{patient_id}/doctor")
def patient_reassign(
    request: Request,
    patient_id: int,
    doctor_id: str = Form(""),
    _: User = Depends(admin_required),
    db: Session = Depends(get_db),
):
    try:
        patient = admin_service.reassign_patient(db, patient_id, _number(doctor_id))
    except AdminError as exc:
        _flash(request, error=str(exc))
    else:
        _flash(
            request,
            f"Пациент: {admin_service.full_name(patient)}. Новый врач: {patient.doctor.name}",
        )
    return _redirect("patients")


@router.get("/doctors", response_class=HTMLResponse)
def doctors_page(
    request: Request,
    _: User = Depends(admin_required),
    db: Session = Depends(get_db),
):
    return _render(request, db, "doctors")


@router.post("/doctors", response_class=HTMLResponse)
def doctor_create(
    request: Request,
    last_name: str = Form(""),
    first_name: str = Form(""),
    patronymic: str = Form(""),
    specialty: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    _: User = Depends(admin_required),
    db: Session = Depends(get_db),
):
    form = {
        "last_name": last_name,
        "first_name": first_name,
        "patronymic": patronymic,
        "specialty": specialty,
        "email": email,
    }
    try:
        doctor = admin_service.create_doctor(
            db, last_name, first_name, patronymic, specialty, email, password
        )
    except AdminError as exc:
        return _render(request, db, "doctors", error=str(exc), form=form)
    _flash(request, f"Учётная запись врача {doctor.name} создана")
    return _redirect("doctors")


@router.post("/doctors/{doctor_id}/status")
def doctor_status(
    request: Request,
    doctor_id: int,
    status: str = Form(""),
    _: User = Depends(admin_required),
    db: Session = Depends(get_db),
):
    try:
        doctor = admin_service.set_doctor_status(db, doctor_id, status)
    except AdminError as exc:
        _flash(request, error=str(exc))
    else:
        message = "Учётная запись разблокирована" if status == "active" else "Учётная запись заблокирована"
        _flash(request, f"{doctor.name}: {message.lower()}")
    return _redirect("doctors")


@router.get("/references/faps", response_class=HTMLResponse)
def faps_page(
    request: Request,
    _: User = Depends(admin_required),
    db: Session = Depends(get_db),
):
    return _render(request, db, "references", reference_kind="faps")


@router.post("/references/faps", response_class=HTMLResponse)
def fap_create(
    request: Request,
    name: str = Form(""),
    village: str = Form(""),
    address: str = Form(""),
    phone: str = Form(""),
    latitude: str = Form(""),
    longitude: str = Form(""),
    _: User = Depends(admin_required),
    db: Session = Depends(get_db),
):
    form = {
        "name": name, "village": village, "address": address,
        "phone": phone, "latitude": latitude, "longitude": longitude,
    }
    try:
        admin_service.create_fap(db, name, village, address, phone, latitude, longitude)
    except AdminError as exc:
        return _render(
            request, db, "references", reference_kind="faps", error=str(exc), form=form
        )
    _flash(request, "ФАП добавлен в справочник")
    return _redirect("faps")


@router.post("/references/faps/{fap_id}")
def fap_update(
    request: Request,
    fap_id: int,
    name: str = Form(""),
    village: str = Form(""),
    address: str = Form(""),
    phone: str = Form(""),
    latitude: str = Form(""),
    longitude: str = Form(""),
    _: User = Depends(admin_required),
    db: Session = Depends(get_db),
):
    try:
        admin_service.update_fap(db, fap_id, name, village, address, phone, latitude, longitude)
    except AdminError as exc:
        _flash(request, error=str(exc))
    else:
        _flash(request, "Данные ФАП сохранены")
    return _redirect("faps")


@router.post("/references/faps/{fap_id}/delete")
def fap_delete(
    request: Request,
    fap_id: int,
    _: User = Depends(admin_required),
    db: Session = Depends(get_db),
):
    try:
        admin_service.delete_fap(db, fap_id)
    except AdminError as exc:
        _flash(request, error=str(exc))
    else:
        _flash(request, "ФАП удалён из справочника")
    return _redirect("faps")


@router.get("/references/drugs", response_class=HTMLResponse)
def drugs_page(
    request: Request,
    _: User = Depends(admin_required),
    db: Session = Depends(get_db),
):
    return _render(request, db, "references", reference_kind="drugs")


@router.post("/references/drugs", response_class=HTMLResponse)
def drug_create(
    request: Request,
    name: str = Form(""),
    _: User = Depends(admin_required),
    db: Session = Depends(get_db),
):
    try:
        admin_service.create_drug(db, name)
    except AdminError as exc:
        return _render(
            request, db, "references", reference_kind="drugs", error=str(exc),
            form={"name": name},
        )
    _flash(request, "Препарат добавлен в справочник")
    return _redirect("drugs")


@router.post("/references/drugs/{drug_id}")
def drug_update(
    request: Request,
    drug_id: int,
    name: str = Form(""),
    _: User = Depends(admin_required),
    db: Session = Depends(get_db),
):
    try:
        admin_service.update_drug(db, drug_id, name)
    except AdminError as exc:
        _flash(request, error=str(exc))
    else:
        _flash(request, "Название препарата сохранено")
    return _redirect("drugs")


@router.post("/references/drugs/{drug_id}/delete")
def drug_delete(
    request: Request,
    drug_id: int,
    _: User = Depends(admin_required),
    db: Session = Depends(get_db),
):
    try:
        admin_service.delete_drug(db, drug_id)
    except AdminError as exc:
        _flash(request, error=str(exc))
    else:
        _flash(request, "Препарат удалён из справочника")
    return _redirect("drugs")
