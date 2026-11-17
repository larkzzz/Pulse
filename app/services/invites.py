"""Коды приглашения: пациент регистрируется по коду, который выдал врач."""


def normalize_code(raw: str) -> str:
    """«pls 4827», «PLS4827», «4827» -> «PLS-4827». Пустая строка, если цифр нет."""
    digits = "".join(c for c in raw if c.isdigit())
    return f"PLS-{digits}" if digits else ""


def check_code(raw: str, codes: list[dict], doctors: list[dict]) -> tuple[dict | None, dict | None, str]:
    """Проверяет код. Возвращает (код, врач, текст ошибки для пациента)."""
    code = normalize_code(raw)
    if not code:
        return None, None, "Введите код с листка, который вам дали в больнице"
    invite = next((c for c in codes if c["code"] == code), None)
    if not invite:
        return None, None, "Код не найден. Проверьте буквы и цифры или позвоните в регистратуру"
    if invite["status"] == "used":
        return None, None, "По этому коду уже зарегистрировались. Если это были не вы, позвоните в регистратуру"
    if invite["status"] in ("expired", "revoked"):
        return None, None, "Срок действия кода закончился. Попросите новый код у врача или в регистратуре"
    doctor = next(d for d in doctors if d["id"] == invite["doctor_id"])
    return invite, doctor, ""
