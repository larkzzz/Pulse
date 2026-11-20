"""Отзываемые сессии для кабинета врача и чата."""
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import LoginSession, User

MAX_AGE = 14 * 24 * 60 * 60


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def end(db: Session, cookie_session: dict) -> None:
    login_id = cookie_session.get("login_id")
    if login_id:
        db.execute(delete(LoginSession).where(LoginSession.id == login_id))
    cookie_session.clear()


def start(db: Session, cookie_session: dict, user_id: int) -> None:
    end(db, cookie_session)
    now = _now()
    db.execute(delete(LoginSession).where(LoginSession.expires_at <= now))
    login_id = secrets.token_urlsafe(36)
    db.add(LoginSession(id=login_id, user_id=user_id, expires_at=now + timedelta(seconds=MAX_AGE)))
    db.commit()
    cookie_session.update(user_id=user_id, login_id=login_id)


def session_users(cookie_session: dict, statuses: tuple[str, ...] = ("active",)):
    return select(User).join(LoginSession, LoginSession.user_id == User.id).where(
        LoginSession.id == cookie_session.get("login_id"), LoginSession.expires_at > _now(),
        User.id == cookie_session.get("user_id"), User.status.in_(statuses),
    )


def session_user(db: Session, cookie_session: dict, statuses: tuple[str, ...] = ("active",)) -> User | None:
    if not cookie_session.get("login_id") or not cookie_session.get("user_id"):
        return None
    return db.scalar(session_users(cookie_session, statuses))
