"""Коды приглашения: пациент регистрируется по коду, который выдал врач."""
from datetime import date


def normalize_code(raw: str) -> str:
    """«pls 4827», «PLS4827», «4827» -> «PLS-4827»."""
    digits = "".join(c for c in raw if c.isdigit())
    return f"PLS-{digits}" if len(digits) == 4 else ""


def check_code(raw: str, invite, doctor) -> str:
    code = normalize_code(raw)
    if not code:
        return "Введите код с листка, который вам дали в больнице"
    if not invite:
        return "Код не найден. Проверьте буквы и цифры или позвоните в регистратуру"
    if invite.status == "used":
        return "По этому коду уже зарегистрировались. Если это были не вы, позвоните в регистратуру"
    if invite.status in ("expired", "revoked") or invite.expires_at < date.today():
        return "Срок действия кода закончился. Попросите новый код у врача или в регистратуре"
    if not doctor:
        return "Код пока не привязан к врачу. Позвоните в регистратуру"
    return ""
