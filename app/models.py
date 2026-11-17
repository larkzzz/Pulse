from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Doctor(Base):
    __tablename__ = "doctors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(100))

    patients: Mapped[list["User"]] = relationship(back_populates="doctor")
    invites: Mapped[list["InviteCode"]] = relationship(back_populates="doctor")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(20), default="patient")
    status: Mapped[str] = mapped_column(String(20), default="active")
    last_name: Mapped[str] = mapped_column(String(100), default="")
    first_name: Mapped[str] = mapped_column(String(100), default="")
    patronymic: Mapped[str] = mapped_column(String(100), default="")
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    phone: Mapped[str] = mapped_column(String(40), default="")
    clinic: Mapped[str] = mapped_column(String(200), default="ГАУЗ «Пестречинская ЦРБ»")
    doctor_id: Mapped[int | None] = mapped_column(ForeignKey("doctors.id"), nullable=True)
    notification_push: Mapped[bool] = mapped_column(Boolean, default=True)
    notification_email: Mapped[bool] = mapped_column(Boolean, default=True)
    notification_voice: Mapped[bool] = mapped_column(Boolean, default=True)

    doctor: Mapped[Doctor | None] = relationship(back_populates="patients")
    medications: Mapped[list["Medication"]] = relationship(
        back_populates="patient", cascade="all, delete-orphan"
    )
    registration_request: Mapped["RegistrationRequest | None"] = relationship(
        foreign_keys="RegistrationRequest.user_id",
        back_populates="patient", cascade="all, delete-orphan"
    )


class InviteCode(Base):
    __tablename__ = "invite_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"))
    patient_hint: Mapped[str] = mapped_column(String(200), default="")
    issued_at: Mapped[date] = mapped_column(Date)
    expires_at: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="new")
    used_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    doctor: Mapped[Doctor] = relationship(back_populates="invites")
    used_by: Mapped[User | None] = relationship(foreign_keys=[used_by_id])


class RegistrationRequest(Base):
    __tablename__ = "registration_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    oms: Mapped[str] = mapped_column(String(30))
    submitted_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    rejection_reason: Mapped[str] = mapped_column(Text, default="")
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    patient: Mapped[User] = relationship(foreign_keys=[user_id], back_populates="registration_request")


class Medication(Base):
    __tablename__ = "medications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    drug: Mapped[str] = mapped_column(String(200))
    dose: Mapped[str] = mapped_column(String(200))
    meal: Mapped[str] = mapped_column(String(30), default="не важно")
    times: Mapped[list[str]] = mapped_column(JSON, default=list)
    until: Mapped[date | None] = mapped_column(Date, nullable=True)

    patient: Mapped[User] = relationship(back_populates="medications")
    intakes: Mapped[list["Intake"]] = relationship(back_populates="medication", cascade="all, delete-orphan")

    @property
    def until_display(self) -> str:
        if not self.until:
            return ""
        months = (
            "января", "февраля", "марта", "апреля", "мая", "июня",
            "июля", "августа", "сентября", "октября", "ноября", "декабря",
        )
        return f"{self.until.day} {months[self.until.month - 1]}"


class Intake(Base):
    __tablename__ = "intakes"
    __table_args__ = (UniqueConstraint("medication_id", "intake_date", "time"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    medication_id: Mapped[int] = mapped_column(ForeignKey("medications.id"), index=True)
    intake_date: Mapped[date] = mapped_column(Date, index=True)
    time: Mapped[str] = mapped_column(String(5))
    taken_at: Mapped[datetime] = mapped_column(DateTime)

    medication: Mapped[Medication] = relationship(back_populates="intakes")
