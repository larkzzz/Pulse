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

engine = create_engine(settings.database_url, connect_args=connect_args, hide_parameters=True, **engine_options)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def migrate_schema(target_engine=engine) -> None:
    """Обновляет уже существующую схему после создания новых таблиц."""
    with target_engine.begin() as connection:
        schema = inspect(connection)
        if "user_id" not in {column["name"] for column in schema.get_columns("doctors")}:
            connection.execute(text("ALTER TABLE doctors ADD COLUMN user_id INTEGER"))
            connection.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_doctors_user_id ON doctors (user_id)"
            ))
        if connection.dialect.name == "sqlite" and schema.has_table("assistant_chats"):
            # В SQLite разработки внешние ключи не включены. PostgreSQL использует ON DELETE CASCADE.
            connection.exec_driver_sql("""
                CREATE TRIGGER IF NOT EXISTS delete_assistant_chat_with_patient
                AFTER DELETE ON users
                BEGIN
                    DELETE FROM assistant_chats WHERE patient_id = OLD.id;
                END
            """)
        if (connection.dialect.name == "sqlite"
                and schema.has_table("doctor_messages") and schema.has_table("login_sessions")):
            connection.exec_driver_sql("""
                CREATE TRIGGER IF NOT EXISTS delete_doctor_chat_with_user
                AFTER DELETE ON users
                BEGIN
                    DELETE FROM doctor_messages WHERE patient_id = OLD.id;
                    UPDATE doctor_messages SET sender_id = NULL WHERE sender_id = OLD.id;
                    DELETE FROM login_sessions WHERE user_id = OLD.id;
                END
            """)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as db:
        yield db
