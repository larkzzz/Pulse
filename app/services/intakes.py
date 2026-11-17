"""Приёмы лекарств на сегодня."""


def today_intakes(schedule: list[dict], taken_today: dict) -> list[dict]:
    """Список приёмов на сегодня по расписанию, по времени.

    Первый непринятый приём — «ближайший» (next), остальные — «позже» (later),
    отмеченные — «принято» (taken).
    """
    items = []
    for entry in schedule:
        for time in entry["times"]:
            taken_at = taken_today.get((entry["id"], time))
            items.append({
                "schedule_id": entry["id"],
                "time": time,
                "key": time.replace(":", ""),
                "drug": entry["drug"],
                "dose": entry["dose"],
                "meal": entry["meal"],
                "status": "taken" if taken_at else "later",
                "taken_at": taken_at,
            })
    items.sort(key=lambda i: i["time"])
    for item in items:
        if item["status"] != "taken":
            item["status"] = "next"
            break
    return items


def day_summary(intakes: list[dict]) -> dict:
    """Ближайший приём и прогресс за день."""
    taken = sum(1 for i in intakes if i["status"] == "taken")
    return {
        "next_intake": next((i for i in intakes if i["status"] == "next"), None),
        "taken_count": taken,
        "total_count": len(intakes),
        "progress_pct": round(taken / len(intakes) * 100) if intakes else 0,
    }
