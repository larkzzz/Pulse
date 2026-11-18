"""Операции кабинета администратора."""
import re
import secrets
from datetime import date, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Doctor, Drug, Fap, InviteCode, RegistrationRequest, User
from app.services.help import has_demo_fap_data, is_demo_fap, phone_href
from app.services.invites import invite_status, transition_invite
from app.services.security import hash_password


class AdminError(Exception):
    pass


def full_name(user: User) -> str:
    return " ".join(part for part in (user.last_name, user.first_name, user.patronymic) if part)


def active_doctors(db: Session) -> list[Doctor]:
    return list(db.scalars(
        select(Doctor)
        .join(Doctor.account)
        .where(User.role == "doctor", User.status == "active")
        .order_by(Doctor.name)
    ).all())


def doctor_rows(db: Session) -> list[dict]:
    doctors = db.scalars(select(Doctor).order_by(Doctor.name)).all()
    return [
        {
            "doctor": doctor,
            "patient_count": sum(
                patient.role == "patient" and patient.status == "active" for patient in doctor.patients
            ),
        }
        for doctor in doctors
    ]


def _active_doctor(db: Session, doctor_id: int, error: str) -> Doctor:
    doctor = db.scalar(
        select(Doctor).join(Doctor.account)
        .where(Doctor.id == doctor_id, User.role == "doctor", User.status == "active")
    )
    if not doctor:
        raise AdminError(error)
    return doctor


def create_invite(
    db: Session, doctor_id: int, patient_hint: str, today: date | None = None
) -> InviteCode:
    doctor = _active_doctor(db, doctor_id, "Выберите врача с действующей учётной записью")
    patient_hint = patient_hint.strip()
    if not patient_hint:
        raise AdminError("Напишите фамилию и инициалы пациента")
    if len(patient_hint) > 200:
        raise AdminError("Фамилия и инициалы должны быть не длиннее 200 знаков")

    today = today or date.today()
    for _ in range(20):
        code = f"PLS-{secrets.randbelow(10000):04d}"
        lookup = select(InviteCode.id).where(InviteCode.code == code)
        if db.scalar(lookup) is not None:
            continue
        invite = InviteCode(
            code=code,
            doctor_id=doctor.id,
            patient_hint=patient_hint,
            issued_at=today,
            expires_at=today + timedelta(days=30),
        )
        db.add(invite)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            if db.scalar(lookup) is None:
                raise
        else:
            return invite
    raise AdminError("Не удалось выдать новый код. Попробуйте ещё раз")


def list_invites(db: Session) -> list[tuple[InviteCode, str]]:
    invites = db.scalars(
        select(InviteCode).order_by(InviteCode.issued_at.desc(), InviteCode.id.desc())
    )
    today = date.today()
    return [(invite, invite_status(invite, today)) for invite in invites]


def revoke_invite(db: Session, invite_id: int) -> InviteCode:
    invite = db.get(InviteCode, invite_id)
    if not invite:
        raise AdminError("Код приглашения не найден")
    if not transition_invite(db, invite_id, "revoked"):
        raise AdminError("Можно отменить только действующий код")
    db.commit()
    return invite


def list_requests(db: Session) -> list[RegistrationRequest]:
    requests = list(db.scalars(select(RegistrationRequest)).all())
    return sorted(
        requests,
        key=lambda item: (item.status != "pending", item.submitted_at, item.id),
        reverse=False,
    )


def review_request(
    db: Session,
    request_id: int,
    admin: User,
    decision: str,
    doctor_id: int = 0,
    rejection_reason: str = "",
) -> RegistrationRequest:
    registration = db.get(RegistrationRequest, request_id)
    if not registration:
        raise AdminError("Заявка не найдена")
    if registration.status != "pending":
        raise AdminError("Эта заявка уже рассмотрена")

    if decision == "approve":
        doctor = _active_doctor(db, doctor_id, "Выберите врача, к которому прикрепить пациента")
        request_status, patient_status, rejection_reason = "approved", "active", ""
    elif decision == "reject":
        rejection_reason = rejection_reason.strip()
        if not rejection_reason:
            raise AdminError("Напишите причину отказа, её увидит пациент")
        request_status = patient_status = "rejected"
    else:
        raise AdminError("Выберите решение по заявке")

    result = db.execute(
        update(RegistrationRequest)
        .where(RegistrationRequest.id == request_id, RegistrationRequest.status == "pending")
        .values(
            status=request_status,
            rejection_reason=rejection_reason,
            reviewed_by_id=admin.id,
            reviewed_at=datetime.now(),
        )
    )
    if result.rowcount != 1:
        raise AdminError("Эта заявка уже рассмотрена")
    if decision == "approve":
        registration.patient.doctor = doctor
    registration.patient.status = patient_status
    db.commit()
    return registration


def list_patients(db: Session) -> list[User]:
    return list(db.scalars(
        select(User)
        .where(User.role == "patient", User.status == "active")
        .order_by(User.last_name, User.first_name, User.patronymic)
    ).all())


def reassign_patient(db: Session, patient_id: int, doctor_id: int) -> User:
    patient = db.get(User, patient_id)
    if not patient or patient.role != "patient" or patient.status != "active":
        raise AdminError("Пациент не найден")
    doctor = _active_doctor(db, doctor_id, "Выберите врача с действующей учётной записью")
    patient.doctor = doctor
    db.commit()
    return patient


def create_doctor(
    db: Session,
    last_name: str,
    first_name: str,
    patronymic: str,
    specialty: str,
    email: str,
    password: str,
) -> Doctor:
    last_name = last_name.strip()
    first_name = first_name.strip()
    patronymic = patronymic.strip()
    specialty = specialty.strip()
    email = email.strip().lower()
    if not last_name or not first_name:
        raise AdminError("Напишите фамилию и имя врача")
    if not specialty:
        raise AdminError("Напишите специальность врача")
    name = " ".join(part for part in (last_name, first_name, patronymic) if part)
    if max(len(last_name), len(first_name), len(patronymic)) > 100 or len(name) > 200:
        raise AdminError("Сократите ФИО врача: до 100 знаков в поле и до 200 всего")
    if len(specialty) > 100:
        raise AdminError("Специальность должна быть не длиннее 100 знаков")
    if len(email) > 320 or not re.fullmatch(r"[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+", email):
        raise AdminError("Проверьте электронную почту врача")
    if db.scalar(select(User.id).where(User.email == email)):
        raise AdminError("Учётная запись с такой почтой уже есть")
    if len(password) < 8:
        raise AdminError("Пароль должен быть не короче 8 знаков")

    account = User(
        email=email,
        password_hash=hash_password(password),
        role="doctor",
        status="active",
        last_name=last_name,
        first_name=first_name,
        patronymic=patronymic,
    )
    doctor = Doctor(
        account=account,
        name=name,
        role=specialty,
    )
    db.add_all([account, doctor])
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if db.scalar(select(User.id).where(User.email == email)) is not None:
            raise AdminError("Учётная запись с такой почтой уже есть") from None
        raise
    return doctor


def set_doctor_status(db: Session, doctor_id: int, status: str) -> Doctor:
    doctor = db.get(Doctor, doctor_id)
    if not doctor or not doctor.account:
        raise AdminError("Учётная запись врача не найдена")
    if status not in ("active", "blocked"):
        raise AdminError("Некорректный статус учётной записи")
    doctor.account.status = status
    db.commit()
    return doctor


def list_faps(db: Session) -> list[Fap]:
    return list(db.scalars(select(Fap).order_by(Fap.village, Fap.name)).all())


def _fap_data(
    name: str,
    village: str,
    address: str,
    phone: str,
    latitude: str,
    longitude: str,
    *,
    replacing_demo: bool = False,
) -> dict:
    data = {
        "name": name.strip(),
        "village": village.strip(),
        "address": address.strip(),
        "phone": phone.strip(),
    }
    if not data["name"] or not data["village"] or not data["address"]:
        raise AdminError("Заполните название, населённый пункт и адрес ФАП")
    if any(len(data[key]) > limit for key, limit in (
        ("name", 200), ("village", 150), ("address", 300), ("phone", 40),
    )):
        raise AdminError("Сократите название, населённый пункт, адрес или телефон ФАП")
    if data["phone"] and not phone_href(data["phone"]):
        raise AdminError("Укажите полный рабочий телефон с +7 или 8 либо оставьте поле пустым")
    try:
        data["latitude"] = float(latitude.replace(",", ".")) if latitude.strip() else None
        data["longitude"] = float(longitude.replace(",", ".")) if longitude.strip() else None
    except ValueError as exc:
        raise AdminError("Координаты должны быть числами") from exc
    if data["latitude"] is not None and not -90 <= data["latitude"] <= 90:
        raise AdminError("Широта должна быть от -90 до 90")
    if data["longitude"] is not None and not -180 <= data["longitude"] <= 180:
        raise AdminError("Долгота должна быть от -180 до 180")
    if (data["latitude"] is None) != (data["longitude"] is None):
        raise AdminError("Укажите и широту, и долготу или оставьте оба поля пустыми")
    if (data["latitude"], data["longitude"]) == (0, 0):
        raise AdminError("Нулевые координаты не подходят. Укажите точку входа в ФАП или оставьте поля пустыми")
    if (replacing_demo and data["latitude"] is None) or has_demo_fap_data(
        data["village"], data["address"], data["latitude"], data["longitude"],
    ):
        raise AdminError("Укажите проверенный адрес и координаты входа в ФАП вместо демо-данных")
    return data


def create_fap(
    db: Session,
    name: str,
    village: str,
    address: str,
    phone: str = "",
    latitude: str = "",
    longitude: str = "",
) -> Fap:
    fap = Fap(**_fap_data(name, village, address, phone, latitude, longitude))
    db.add(fap)
    db.commit()
    return fap


def update_fap(
    db: Session,
    fap_id: int,
    name: str,
    village: str,
    address: str,
    phone: str = "",
    latitude: str = "",
    longitude: str = "",
) -> Fap:
    fap = db.get(Fap, fap_id)
    if not fap:
        raise AdminError("ФАП не найден")
    data = _fap_data(name, village, address, phone, latitude, longitude, replacing_demo=is_demo_fap(fap))
    for key, value in data.items():
        setattr(fap, key, value)
    db.commit()
    return fap


def delete_fap(db: Session, fap_id: int) -> None:
    fap = db.get(Fap, fap_id)
    if not fap:
        raise AdminError("ФАП не найден")
    db.delete(fap)
    db.commit()


def list_drugs(db: Session) -> list[Drug]:
    return list(db.scalars(select(Drug).order_by(Drug.name)).all())


def _validate_drug(db: Session, name: str, drug_id: int = 0) -> str:
    name = name.strip()
    if not name:
        raise AdminError("Напишите название препарата")
    if len(name) > 200:
        raise AdminError("Название препарата должно быть не длиннее 200 знаков")
    normalized = name.casefold()
    names = db.scalars(select(Drug.name).where(Drug.id != drug_id))
    if any(existing.casefold() == normalized for existing in names):
        raise AdminError("Такой препарат уже есть в справочнике")
    return name


def _save_drug(db: Session, name: str, drug_id: int = 0) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        _validate_drug(db, name, drug_id)
        raise


def create_drug(db: Session, name: str) -> Drug:
    drug = Drug(name=_validate_drug(db, name))
    db.add(drug)
    _save_drug(db, drug.name)
    return drug


def update_drug(db: Session, drug_id: int, name: str) -> Drug:
    drug = db.get(Drug, drug_id)
    if not drug:
        raise AdminError("Препарат не найден")
    drug.name = _validate_drug(db, name, drug_id)
    _save_drug(db, drug.name, drug_id)
    return drug


def delete_drug(db: Session, drug_id: int) -> None:
    drug = db.get(Drug, drug_id)
    if not drug:
        raise AdminError("Препарат не найден")
    db.delete(drug)
    db.commit()
