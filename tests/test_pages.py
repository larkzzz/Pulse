import pytest


@pytest.mark.parametrize("url", [
    "/", "/cabinet", "/login", "/register", "/register?code=PLS-4827", "/register/no-code", "/pending",
])
def test_pages_open(client, url):
    assert client.get(url).status_code == 200


def test_take_intake_marks_next(client):
    r = client.post("/intakes/2/1300/take")
    assert r.status_code == 200 and "Метформин принят" in r.text


def test_add_medication_requires_fields(client):
    r = client.post("/cabinet/medications", data={})
    assert "Напишите название лекарства" in r.text


def test_add_medication(client):
    r = client.post("/cabinet/medications", data={"drug": "Амлодипин", "dose": "5 мг", "time1": "09:30"})
    assert "Амлодипин добавлен" in r.text


def test_login_redirects_by_status(client):
    r = client.post("/login", data={"email": "galina.smirnova@mail.ru", "password": "x"})
    assert r.headers["HX-Redirect"] == "/"
    r = client.post("/login", data={"email": "petrov@mail.ru", "password": "x"})
    assert r.headers["HX-Redirect"] == "/pending"


def _profile(**extra):
    return {"last_name": "Иванов", "first_name": "Пётр", "birth_date": "1948-05-02",
            "phone": "+7 917 111-22-33", "email": "ivanov@mail.ru",
            "password": "secret123", "password2": "secret123", "consent": "1", **extra}


def test_register_by_code_assigns_doctor(client):
    r = client.post("/register", data=_profile(mode="code", code="pls 3159"))
    assert r.headers["HX-Redirect"] == "/cabinet?welcome=1"
    assert "Гарипова Алсу Маратовна" in client.get("/cabinet?welcome=1").text
    # тот же код второй раз не подходит
    r = client.post("/register", data=_profile(mode="code", code="PLS-3159", email="other@mail.ru"))
    assert "уже зарегистрировались" in r.text


def test_register_request_without_code(client):
    r = client.post("/register", data=_profile(mode="request", oms="123"))
    assert "16 цифр" in r.text
    r = client.post("/register", data=_profile(mode="request", oms="1655 1111 2222 3333"))
    assert r.headers["HX-Redirect"] == "/pending"
