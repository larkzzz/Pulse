"""Сигнализируем WebSocket о новых сообщениях. Тексты остаются в базе."""
import asyncio
import logging
from contextlib import asynccontextmanager, contextmanager
from threading import Event, Lock

import psycopg
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

_CHANNEL = "pulse_doctor_chat"
_logger = logging.getLogger(__name__)
_lock = Lock()
_subscribers: dict[int, dict[asyncio.Event, asyncio.AbstractEventLoop]] = {}


@contextmanager
def subscribe(patient_id: int):
    loop, ready = asyncio.get_running_loop(), asyncio.Event()
    with _lock:
        _subscribers.setdefault(patient_id, {})[ready] = loop
    try:
        yield ready
    finally:
        with _lock:
            watchers = _subscribers[patient_id]
            del watchers[ready]
            if not watchers:
                del _subscribers[patient_id]


def _wake(patient_id: int | None = None) -> None:
    with _lock:
        groups = _subscribers.values() if patient_id is None else (_subscribers.get(patient_id, {}),)
        watchers = [item for group in groups for item in group.items()]
    for ready, loop in watchers:
        try:
            loop.call_soon_threadsafe(ready.set)
        except RuntimeError:
            # Тестовый клиент или процесс мог закрыть свой цикл событий.
            continue


def commit_message(db: Session, patient_id: int) -> None:
    postgres = db.get_bind().dialect.name == "postgresql"
    if postgres:
        db.execute(select(func.pg_notify(_CHANNEL, str(patient_id))))
    db.commit()
    if not postgres:
        _wake(patient_id)


def _listen(engine: Engine, stopped: Event) -> None:
    args, kwargs = engine.dialect.create_connect_args(engine.url)
    kwargs.setdefault("connect_timeout", 3)
    kwargs["autocommit"] = True
    while not stopped.is_set():
        try:
            with psycopg.connect(*args, **kwargs) as connection:
                connection.execute(f"LISTEN {_CHANNEL}")
                _wake()  # Перечитать историю, если запись произошла во время переподключения.
                while not stopped.is_set():
                    for notice in connection.notifies(timeout=1.0, stop_after=1):
                        if notice.payload.isascii() and notice.payload.isdecimal() and len(notice.payload) <= 10:
                            _wake(int(notice.payload))
        except psycopg.Error:
            _logger.warning("Связь с каналом обновлений чата потеряна. Попробуем подключиться снова.")
            stopped.wait(2.0)


@asynccontextmanager
async def listen(engine: Engine):
    if engine.dialect.name != "postgresql":
        yield
        return
    stopped = Event()
    listener = asyncio.create_task(asyncio.to_thread(_listen, engine, stopped))
    try:
        yield
    finally:
        stopped.set()
        await listener
