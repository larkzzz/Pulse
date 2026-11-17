"""Приёмы лекарств на сегодня."""
from datetime import date


def today_intakes(schedule: list[dict], taken_today: dict) -> list[dict]:
    """Список приёмов на сегодня по расписанию, по времени."""
    items = []
    for entry in schedule:
        for intake_time in entry["times"]:
            taken_at = taken_today.get((entry["id"], intake_time))
            items.append({
                "schedule_id": entry["id"],
                "time": intake_time,
                "key": intake_time.replace(":", ""),
                "drug": entry["drug"],
                "dose": entry["dose"],
                "meal": entry["meal"],
                "status": "taken" if taken_at else "later",
                "taken_at": taken_at,
            })
    items.sort(key=lambda item: item["time"])
    for item in items:
        if item["status"] != "taken":
            item["status"] = "next"
            break
    return items


def day_summary(intakes: list[dict]) -> dict:
    """Ближайший приём и прогресс за день."""
    taken = sum(1 for item in intakes if item["status"] == "taken")
    return {
        "next_intake": next((item for item in intakes if item["status"] == "next"), None),
        "taken_count": taken,
        "total_count": len(intakes),
        "progress_pct": round(taken / len(intakes) * 100) if intakes else 0,
    }


def today_label(value: date | None = None) -> str:
    value = value or date.today()
    months = (
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    )
    weekdays = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
    return f"{value.day} {months[value.month - 1]}, {weekdays[value.weekday()]}"
