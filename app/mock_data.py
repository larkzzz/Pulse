"""Тестовые данные для макета. Позже их заменит база данных."""

PATIENT = {
    "id": 1,
    "first_name": "Галина",
    "patronymic": "Петровна",
    "doctor": "Ильдар Ринатович Сафин",
    "doctor_role": "терапевт",
}

# Приёмы лекарств на сегодня. status: "taken" — принял, "next" — ближайший, "later" — позже.
TODAY_DATE = "8 ноября, воскресенье"

INTAKES = [
    {"id": 1, "time": "08:00", "drug": "Эналаприл", "dose": "10 мг, 1 таблетка", "note": "до еды", "status": "taken", "taken_at": "08:05"},
    {"id": 2, "time": "13:00", "drug": "Метформин", "dose": "500 мг, 1 таблетка", "note": "во время еды", "status": "next", "taken_at": None},
    {"id": 3, "time": "19:00", "drug": "Аторвастатин", "dose": "20 мг, 1 таблетка", "note": "после еды", "status": "later", "taken_at": None},
    {"id": 4, "time": "21:00", "drug": "Эналаприл", "dose": "10 мг, 1 таблетка", "note": "до еды", "status": "later", "taken_at": None},
]
