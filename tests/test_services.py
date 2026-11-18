from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from threading import Barrier
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import InviteAttempt
from app.services.accounts import oms_is_valid, validate_profile
from app.services.intakes import day_summary, today_intakes, today_label
from app.services.invites import BLOCKED_MESSAGE, check_code, limited_code_status, normalize_code
from app.services.security import hash_password, verify_password

DOCTOR = SimpleNamespace(id=1, name="Сафин Ильдар Ринатович", role="врач-терапевт")


def _invite(status="new", expires_at=None):
    return SimpleNamespace(
        code="PLS-4827", doctor_id=1, status=status,
        expires_at=expires_at or date.today() + timedelta(days=30),
    )


def test_normalize_code_accepts_any_spelling():
    for raw in ("PLS-4827", "pls4827", "pls 4827", " 4827 "):
        assert normalize_code(raw) == "PLS-4827"
    assert normalize_code("abc") == ""
    assert normalize_code("PLS-48270") == ""


def test_check_code():
    assert check_code("pls4827", _invite(), DOCTOR) == ""
    assert "уже зарегистрировались" in check_code("PLS-1206", _invite("used"), DOCTOR)
    assert "Срок действия" in check_code("PLS-0988", _invite("expired"), DOCTOR)
    assert "Срок действия" in check_code(
        "PLS-0988", _invite(expires_at=date.today() - timedelta(days=1)), DOCTOR
    )
    assert "не найден" in check_code("PLS-9999", None, None)
    assert "Введите код" in check_code("", None, None)


def test_concurrent_invite_failures_use_one_shared_counter(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'attempts.db'}")
    InviteAttempt.__table__.create(engine)
    start = Barrier(8)

    def check(i):
        start.wait(timeout=5)
        with Session(engine) as db:
            return limited_code_status(
                db, "неверный", ("shared-ip", f"session-{i}"), now=datetime(2026, 11, 18),
            )[2]

    try:
        with ThreadPoolExecutor(max_workers=8) as workers:
            errors = list(workers.map(check, range(8)))
        assert errors.count(BLOCKED_MESSAGE) == 4
        with Session(engine) as db:
            assert db.get(InviteAttempt, "shared-ip").failures == 5
    finally:
        engine.dispose()


def test_today_intakes_order_and_next():
    schedule = [
        {"id": 1, "drug": "А", "dose": "1", "meal": "до еды", "times": ["21:00", "08:00"]},
        {"id": 2, "drug": "Б", "dose": "1", "meal": "после еды", "times": ["13:00"]},
    ]
    items = today_intakes(schedule, {(1, "08:00"): "08:05"})
    assert [item["time"] for item in items] == ["08:00", "13:00", "21:00"]
    assert [item["status"] for item in items] == ["taken", "next", "later"]
    summary = day_summary(items)
    assert summary["next_intake"]["time"] == "13:00"
    assert (summary["taken_count"], summary["total_count"], summary["progress_pct"]) == (1, 3, 33)


def test_day_summary_empty():
    assert day_summary([]) == {"next_intake": None, "taken_count": 0, "total_count": 0, "progress_pct": 0}


def test_today_label():
    assert today_label(date(2026, 11, 8)) == "8 ноября, воскресенье"


def test_validate_profile():
    form = {"last_name": "Иванов", "first_name": "Пётр", "birth_date": "1950-01-01",
            "phone": "+7 917 111-22-33", "email": "a@mail.ru", "consent": True}
    assert validate_profile(form, "secret123", "secret123", set()) == {}
    assert "password2" in validate_profile(form, "secret123", "other123", set())
    assert "email" in validate_profile(form, "secret123", "secret123", {"a@mail.ru"})
    assert "consent" in validate_profile({**form, "consent": False}, "secret123", "secret123", set())
    assert "birth_date" in validate_profile({**form, "birth_date": "tomorrow"}, "secret123", "secret123", set())
    assert oms_is_valid("1655 1111 2222 3333")
    assert not oms_is_valid("123")
    assert not oms_is_valid("1655x1111x2222x3333")


def test_password_hashing():
    stored = hash_password("secret123")
    assert "secret123" not in stored
    assert verify_password("secret123", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("secret123", "broken")
