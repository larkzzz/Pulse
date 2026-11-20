from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.services import sessions


class AuthenticationRequired(Exception):
    pass


class AccessDenied(Exception):
    pass


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = sessions.session_user(db, request.session)
    if not user:
        raise AuthenticationRequired
    return user


def require_roles(*roles: str):
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise AccessDenied
        return user

    return dependency
