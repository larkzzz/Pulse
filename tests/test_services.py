from app.services.accounts import oms_is_valid, validate_profile
from app.services.intakes import day_summary, today_intakes
from app.services.invites import check_code, normalize_code

DOCTORS = [{"id": 1, "name": "Сафин Ильдар Ринатович", "role": "врач-терапевт"}]
CODES = [
    {"code": "PLS-4827", "doctor_id": 1, "status": "new"},
    {"code": "PLS-1206", "doctor_id": 1, "status": "used"},
    {"code": "PLS-0988", "doctor_id": 1, "status": "expired"},
]


def test_normalize_code_accepts_any_spelling():
    for raw in ("PLS-4827", "pls4827", "pls 4827", " 4827 "):
        assert normalize_code(raw) == "PLS-4827"
    assert normalize_code("abc") == ""


def test_check_code():
    invite, doctor, error = check_code("pls4827", CODES, DOCTORS)
    assert invite["code"] == "PLS-4827" and doctor["id"] == 1 and error == ""
    assert "уже зарегистрировались" in check_code("PLS-1206", CODES, DOCTORS)[2]
    assert "Срок действия" in check_code("PLS-0988", CODES, DOCTORS)[2]
    assert "не найден" in check_code("PLS-9999", CODES, DOCTORS)[2]
    assert "Введите код" in check_code("", CODES, DOCTORS)[2]


def test_today_intakes_order_and_next():
    schedule = [
        {"id": 1, "drug": "А", "dose": "1", "meal": "до еды", "times": ["21:00", "08:00"]},
        {"id": 2, "drug": "Б", "dose": "1", "meal": "после еды", "times": ["13:00"]},
    ]
    items = today_intakes(schedule, {(1, "08:00"): "08:05"})
    assert [i["time"] for i in items] == ["08:00", "13:00", "21:00"]
    assert [i["status"] for i in items] == ["taken", "next", "later"]
    summary = day_summary(items)
    assert summary["next_intake"]["time"] == "13:00"
    assert (summary["taken_count"], summary["total_count"], summary["progress_pct"]) == (1, 3, 33)


def test_day_summary_empty():
    assert day_summary([]) == {"next_intake": None, "taken_count": 0, "total_count": 0, "progress_pct": 0}


def test_validate_profile():
    form = {"last_name": "Иванов", "first_name": "Пётр", "birth_date": "1950-01-01",
            "phone": "+7 917 111-22-33", "email": "a@mail.ru", "consent": True}
    assert validate_profile(form, "secret123", "secret123", set()) == {}
    assert "password2" in validate_profile(form, "secret123", "other123", set())
    assert "email" in validate_profile(form, "secret123", "secret123", {"a@mail.ru"})
    assert "consent" in validate_profile({**form, "consent": False}, "secret123", "secret123", set())
    assert oms_is_valid("1655 1111 2222 3333") and not oms_is_valid("123")
