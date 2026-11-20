"""Доступ к переписке и хранение сообщений по пациенту, независимо от смены врача."""
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, literal, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, aliased

from app.models import Doctor, DoctorMessage, User
from app.services import chat_events
from app.services.sessions import session_users

MAX_MESSAGE_LENGTH = 2000
PAGE_SIZE = 20


class MessageError(ValueError):
    pass


class SubmissionConflict(MessageError):
    pass


class ChatUnavailable(MessageError):
    pass


@dataclass
class Room:
    doctor_id: int
    viewer_id: int
    messages: list[DoctorMessage]
    older: int | None

    @property
    def version(self) -> tuple[tuple[int, ...], bool]:
        return tuple(message.id for message in self.messages), self.older is not None


def assigned_patients(db: Session, doctor_id: int) -> list[User]:
    return list(db.scalars(
        select(User).where(
            User.doctor_id == doctor_id, User.role == "patient", User.status == "active",
        ).order_by(User.last_name, User.first_name, User.patronymic, User.id)
    ))


def active_doctor(db: Session, patient: User) -> Doctor | None:
    return db.scalar(
        select(Doctor).join(Doctor.account).where(
            Doctor.id == patient.doctor_id, User.role == "doctor", User.status == "active",
        )
    ) if patient.doctor_id is not None else None


def can_chat(db: Session, viewer: User, patient_id: int) -> bool:
    if viewer.role == "patient":
        return viewer.id == patient_id and active_doctor(db, viewer) is not None
    if viewer.role == "doctor":
        return db.scalar(
            select(User.id).join(Doctor, Doctor.id == User.doctor_id).where(
                User.id == patient_id, User.role == "patient", User.status == "active",
                Doctor.user_id == viewer.id,
            )
        ) is not None
    return False


def history(db: Session, patient_id: int, before: int | None = None) -> tuple[list[DoctorMessage], int | None]:
    query = select(DoctorMessage).where(DoctorMessage.patient_id == patient_id)
    if before is not None:
        query = query.where(DoctorMessage.id < before)
    rows = list(db.scalars(query.order_by(DoctorMessage.id.desc()).limit(PAGE_SIZE + 1)))
    return _page(rows)


def _page(rows: list[DoctorMessage]) -> tuple[list[DoctorMessage], int | None]:
    return rows[:PAGE_SIZE], rows[PAGE_SIZE - 1].id if len(rows) > PAGE_SIZE else None


def _room_access(cookie: dict, patient_id: int, doctor_id: int | None):
    patient, account = aliased(User), aliased(User)
    query = (
        session_users(cookie)
        .join(patient, patient.id == patient_id)
        .join(Doctor, Doctor.id == patient.doctor_id)
        .join(account, account.id == Doctor.user_id)
        .where(
            patient.role == "patient", patient.status == "active",
            account.role == "doctor", account.status == "active",
            or_(and_(User.role == "patient", User.id == patient.id),
                and_(User.role == "doctor", User.id == account.id)),
        )
    )
    return query.where(Doctor.id == doctor_id) if doctor_id is not None else query


def room(db: Session, cookie: dict, patient_id: int, doctor_id: int | None) -> Room | None:
    # Проверяем доступ и выбираем сообщения одним запросом, в одном снимке данных.
    query = (
        _room_access(cookie, patient_id, doctor_id)
        .outerjoin(DoctorMessage, DoctorMessage.patient_id == patient_id)
        .with_only_columns(Doctor.id, User.id, DoctorMessage, maintain_column_froms=True)
        .order_by(DoctorMessage.id.desc()).limit(PAGE_SIZE + 1)
    )
    rows = db.execute(query).all()
    if not rows:
        return None
    messages, older = _page([row[2] for row in rows if row[2] is not None])
    return Room(rows[0][0], rows[0][1], messages, older)


def room_version(db: Session, cookie: dict, patient_id: int, doctor_id: int) -> tuple[tuple[int, ...], bool] | None:
    """Резервная сверка без загрузки текстов. Пустой кортеж означает доступный пустой чат."""
    query = (
        _room_access(cookie, patient_id, doctor_id)
        .outerjoin(DoctorMessage, DoctorMessage.patient_id == patient_id)
        .with_only_columns(DoctorMessage.id, maintain_column_froms=True)
        .order_by(DoctorMessage.id.desc()).limit(PAGE_SIZE + 1)
    )
    ids = list(db.scalars(query))
    if not ids:
        return None
    return tuple(message_id for message_id in ids[:PAGE_SIZE] if message_id is not None), len(ids) > PAGE_SIZE


def save_message(
    db: Session, cookie: dict, patient_id: int, doctor_id: int, submission_id: str, text: str,
) -> None:
    """Вставляет только при действующем доступе. Повтор отправки не создаёт новое сообщение."""
    if not re.fullmatch(r"[0-9a-f]{32}", submission_id):
        raise MessageError("Форма устарела. Обновите страницу и попробуйте ещё раз.")
    text = text.strip()
    if not text:
        raise MessageError("Напишите сообщение.")
    if len(text) > MAX_MESSAGE_LENGTH:
        raise MessageError(f"Сократите сообщение до {MAX_MESSAGE_LENGTH} знаков.")
    if "\0" in text or any("\ud800" <= char <= "\udfff" for char in text):
        raise MessageError("Уберите необычные символы из сообщения и попробуйте ещё раз.")

    source = _room_access(cookie, patient_id, doctor_id).with_only_columns(
        literal(patient_id), User.id, User.role, literal(submission_id), literal(text),
        literal(datetime.now(UTC).replace(tzinfo=None)), maintain_column_froms=True,
    )
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    statement = (
        insert(DoctorMessage)
        .from_select(["patient_id", "sender_id", "sender_role", "submission_id", "text", "sent_at"], source)
        .on_conflict_do_nothing(index_elements=[DoctorMessage.sender_id, DoctorMessage.submission_id])
        .returning(DoctorMessage.id)
    )
    # Для INSERT … SELECT драйвер может вернуть rowcount=-1. Подтверждаем по вставленной строке.
    if db.scalar(statement) is None:
        allowed = _room_access(cookie, patient_id, doctor_id).with_only_columns(
            User.id, maintain_column_froms=True,
        )
        if db.scalar(allowed) is None:
            raise ChatUnavailable("Доступ к чату изменился. Скопируйте черновик и обновите страницу.")
        previous = db.scalar(select(DoctorMessage).where(
            DoctorMessage.sender_id == cookie.get("user_id"), DoctorMessage.submission_id == submission_id,
        ))
        if not previous or previous.patient_id != patient_id or previous.text != text:
            raise SubmissionConflict("Этот текст не отправлен. Черновик сохранён. Проверьте его и нажмите «Отправить».")
        return
    chat_events.commit_message(db, patient_id)
