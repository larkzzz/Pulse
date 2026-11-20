"""Коды приглашения: пациент регистрируется по коду, который выдал врач."""
import secrets
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import case, delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import InviteAttempt, InviteCode

MAX_FAILURES = 5
ATTEMPT_WINDOW = timedelta(minutes=15)
BLOCKED_MESSAGE = (
    "Слишком много неверных попыток. Попробуйте через 15 минут "
    "или позвоните в регистратуру"
)
INVITE_STATUSES = {
    "new": "Действует",
    "used": "Использован",
    "revoked": "Отменён",
    "expired": "Истёк",
}


class InviteError(ValueError):
    pass


def normalize_code(raw: str) -> str:
    """«pls 4827», «PLS4827», «4827» -> «PLS-4827»."""
    digits = "".join(c for c in raw if c.isdigit())
    return f"PLS-{digits}" if len(digits) == 4 else ""


def invite_status(invite: InviteCode, today: date) -> str:
    return "expired" if invite.status == "new" and invite.expires_at < today else invite.status


def transition_invite(db: Session, invite_id: int, status: str, **changes) -> bool:
    """Меняет только действующий код; вызывающий фиксирует общую транзакцию."""
    result = db.execute(
        update(InviteCode)
        .where(
            InviteCode.id == invite_id,
            InviteCode.status == "new",
            InviteCode.expires_at >= date.today(),
        )
        .values(status=status, **changes)
    )
    return result.rowcount == 1


def issue_invite(db: Session, doctor_id: int, patient_hint: str, today: date | None = None) -> InviteCode:
    patient_hint = patient_hint.strip()
    if not patient_hint:
        raise InviteError("Напишите фамилию и инициалы пациента или его полное имя")
    if len(patient_hint) > 200:
        raise InviteError("Имя пациента должно быть не длиннее 200 знаков")
    if "\0" in patient_hint:
        raise InviteError("Проверьте имя пациента")

    today = today or date.today()
    for _ in range(20):
        code = f"PLS-{secrets.randbelow(10000):04d}"
        lookup = select(InviteCode.id).where(InviteCode.code == code)
        if db.scalar(lookup) is not None:
            continue
        invite = InviteCode(
            code=code, doctor_id=doctor_id, patient_hint=patient_hint,
            issued_at=today, expires_at=today + timedelta(days=30),
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
    raise InviteError("Не удалось выдать новый код. Попробуйте ещё раз")


def list_invites(db: Session, doctor_id: int | None = None) -> list[tuple[InviteCode, str]]:
    query = select(InviteCode).order_by(InviteCode.issued_at.desc(), InviteCode.id.desc())
    if doctor_id is not None:
        query = query.where(InviteCode.doctor_id == doctor_id)
    today = date.today()
    return [(invite, invite_status(invite, today)) for invite in db.scalars(query)]


def revoke_invite(db: Session, invite: InviteCode) -> None:
    if not transition_invite(db, invite.id, "revoked"):
        raise InviteError("Можно отменить только действующий код")
    db.commit()


def check_code(raw: str, invite, doctor) -> str:
    code = normalize_code(raw)
    if not code:
        return "Введите код с листка, который вам дали в больнице"
    if not invite:
        return "Код не найден. Проверьте буквы и цифры или позвоните в регистратуру"
    if invite.status == "used":
        return "По этому коду уже зарегистрировались. Если это были не вы, позвоните в регистратуру"
    if invite_status(invite, date.today()) in ("expired", "revoked"):
        return "Срок действия кода закончился. Попросите новый код у врача или в регистратуре"
    if not doctor:
        return "Код пока не привязан к врачу. Позвоните в регистратуру"
    return ""


def code_status(db: Session, raw: str):
    code = normalize_code(raw)
    invite = db.scalar(select(InviteCode).where(InviteCode.code == code)) if code else None
    doctor = invite.doctor if invite else None
    error = check_code(raw, invite, doctor)
    return invite, doctor if not error else None, error


def limited_code_status(db: Session, raw: str, keys: tuple[str, ...], *, now: datetime | None = None):
    """Проверяет код и обновляет счётчики в общей транзакции до возврата результата."""
    if not raw.strip():
        return code_status(db, raw)

    now = now or datetime.now(UTC).replace(tzinfo=None)
    expires_at = now + ATTEMPT_WINDOW
    # Удаляем старые данные отдельно, не удерживая блокировки при проверке кода.
    db.execute(delete(InviteAttempt).where(InviteAttempt.expires_at <= now))
    db.commit()

    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    counts = []
    for key in keys:  # Во всех запросах сначала IP, затем сессия.
        # UPSERT удерживает блокировку до commit, чтобы запросы не прочитали один и тот же счётчик.
        statement = insert(InviteAttempt).values(key=key, failures=0, expires_at=expires_at)
        failures = db.execute(
            statement.on_conflict_do_update(
                index_elements=[InviteAttempt.key],
                set_={"failures": case(
                    (InviteAttempt.expires_at <= now, 0), else_=InviteAttempt.failures,
                )},
            ).returning(InviteAttempt.failures)
        ).scalar_one()
        if failures >= MAX_FAILURES:
            db.rollback()
            return None, None, BLOCKED_MESSAGE
        counts.append(failures)

    invite, doctor, error = code_status(db, raw)
    if not error:
        db.execute(delete(InviteAttempt).where(InviteAttempt.key.in_(keys)))
    else:
        db.execute(
            update(InviteAttempt).where(InviteAttempt.key.in_(keys))
            .values(failures=InviteAttempt.failures + 1, expires_at=expires_at)
        )
    db.commit()

    if error and max(counts) + 1 >= MAX_FAILURES:
        return None, None, BLOCKED_MESSAGE
    return invite, doctor, error
