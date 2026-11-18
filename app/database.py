from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings


class Base(DeclarativeBase):
    pass


connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine_options = {}
if settings.database_url == "sqlite:///:memory:":
    engine_options["poolclass"] = StaticPool

engine = create_engine(settings.database_url, connect_args=connect_args, **engine_options)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def migrate_schema(target_engine=engine) -> None:
    """Добавляет поля новых этапов в уже существующую базу разработки."""
    if "user_id" in {column["name"] for column in inspect(target_engine).get_columns("doctors")}:
        return
    with target_engine.begin() as connection:
        connection.execute(text("ALTER TABLE doctors ADD COLUMN user_id INTEGER"))
        connection.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_doctors_user_id ON doctors (user_id)"
        ))


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as db:
        yield db
