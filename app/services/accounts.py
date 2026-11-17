"""Проверка данных при регистрации и в профиле."""
from datetime import date


def validate_profile(form: dict, password: str, password2: str, taken_emails: set[str]) -> dict:
    """Ошибки в полях регистрации: {имя поля: понятный текст}."""
    errors = {}
    if not form["last_name"]:
        errors["last_name"] = "Напишите фамилию"
    if not form["first_name"]:
        errors["first_name"] = "Напишите имя"
    try:
        birth_date = date.fromisoformat(form["birth_date"])
        if birth_date >= date.today():
            errors["birth_date"] = "Проверьте дату рождения"
    except ValueError:
        errors["birth_date"] = "Укажите дату рождения"
    if sum(c.isdigit() for c in form["phone"]) < 10:
        errors["phone"] = "Напишите номер телефона полностью, например +7 917 555-12-34"
    if "@" not in form["email"]:
        errors["email"] = "Нужна почта, на неё будут приходить напоминания"
    elif form["email"] in taken_emails:
        errors["email"] = "Такая почта уже зарегистрирована. Попробуйте войти"
    if len(password) < 8:
        errors["password"] = "Пароль должен быть не короче 8 знаков"
    elif password != password2:
        errors["password2"] = "Пароли не совпадают"
    if not form["consent"]:
        errors["consent"] = "Без согласия мы не можем хранить ваши данные"
    return errors


def oms_is_valid(oms: str) -> bool:
    """В номере полиса ОМС единого образца 16 цифр, пробелы допустимы."""
    return (
        sum(c.isdigit() for c in oms) == 16
        and all(c.isdigit() or c.isspace() for c in oms)
    )
