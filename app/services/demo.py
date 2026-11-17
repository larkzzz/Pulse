from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Doctor, Intake, InviteCode, Medication, RegistrationRequest, User
from app.services.security import hash_password

DEMO_PASSWORD = "demo1234"


def seed_demo_data(db: Session) -> None:
    if db.scalar(select(User.id).limit(1)):
        return

    safin = Doctor(name="Сафин Ильдар Ринатович", role="врач-терапевт")
    garipova = Doctor(name="Гарипова Алсу Маратовна", role="врач-кардиолог")
    db.add_all([safin, garipova])
    db.flush()

    galina = User(
        email="galina.smirnova@mail.ru",
        password_hash=hash_password(DEMO_PASSWORD),
        role="patient",
        status="active",
        last_name="Смирнова",
        first_name="Галина",
        patronymic="Петровна",
        birth_date=date(1950, 5, 12),
        phone="+7 917 555-12-34",
        doctor=safin,
    )
    petrov = User(
        email="petrov@mail.ru",
        password_hash=hash_password(DEMO_PASSWORD),
        role="patient",
        status="pending",
        last_name="Петров",
        first_name="Олег",
        patronymic="Иванович",
        birth_date=date(1951, 3, 14),
        phone="+7 917 222-33-44",
    )
    db.add_all([
        galina,
        petrov,
        User(email="safin@pestrechi-crb.ru", password_hash=hash_password(DEMO_PASSWORD), role="doctor"),
        User(email="admin@pestrechi-crb.ru", password_hash=hash_password(DEMO_PASSWORD), role="admin"),
    ])
    db.flush()

    today = date.today()
    db.add_all([
        InviteCode(
            code="PLS-4827", doctor=safin, patient_hint="Иванов П. С.",
            issued_at=today - timedelta(days=3), expires_at=today + timedelta(days=27),
        ),
        InviteCode(
            code="PLS-3159", doctor=garipova, patient_hint="Хасанова Р. И.",
            issued_at=today - timedelta(days=1), expires_at=today + timedelta(days=29),
        ),
        InviteCode(
            code="PLS-1206", doctor=safin, patient_hint="Смирнова Г. П.",
            issued_at=today - timedelta(days=20), expires_at=today + timedelta(days=10),
            status="used", used_by=galina,
        ),
        InviteCode(
            code="PLS-0988", doctor=safin, patient_hint="Николаев А. В.",
            issued_at=today - timedelta(days=70), expires_at=today - timedelta(days=40),
            status="expired",
        ),
    ])
    db.add(RegistrationRequest(
        patient=petrov, oms="1655 4321 0987 6543", submitted_at=datetime.now(), status="pending"
    ))

    enalapril = Medication(
        patient=galina, drug="Эналаприл", dose="10 мг, 1 таблетка",
        meal="до еды", times=["08:00", "21:00"],
    )
    db.add_all([
        enalapril,
        Medication(
            patient=galina, drug="Метформин", dose="500 мг, 1 таблетка",
            meal="во время еды", times=["13:00"], until=date(today.year, 12, 31),
        ),
        Medication(
            patient=galina, drug="Аторвастатин", dose="20 мг, 1 таблетка",
            meal="после еды", times=["19:00"],
        ),
    ])
    db.flush()
    db.add(Intake(
        medication=enalapril, intake_date=today, time="08:00",
        taken_at=datetime.combine(today, time(8, 5)),
    ))
    db.commit()
