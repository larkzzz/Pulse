"""Настройки приложения.

Значения берутся из переменных окружения или из файла .env в корне проекта.
Образец всех настроек — в .env.example. Сам .env в git не попадает: там ключи и пароли.
"""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR.parent / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class Settings:
    # Общие
    app_env: str = _env("APP_ENV", "dev")            # dev — разработка, prod — боевой сервер
    secret_key: str = _env("SECRET_KEY", "dev-secret-change-me")
    base_url: str = _env("BASE_URL", "http://localhost:8000")  # для ссылок в письмах и QR-кодах
    database_url: str = _env("DATABASE_URL", "sqlite:///./pulse.db")

    # Данные больницы
    registry_phone: str = _env("REGISTRY_PHONE")

    # Yandex Cloud: YandexGPT (ИИ-помощник) и SpeechKit (озвучка напоминаний)
    yandex_folder_id: str = _env("YANDEX_FOLDER_ID")
    yandex_api_key: str = _env("YANDEX_API_KEY")

    # Яндекс Карты (JavaScript API) — только встроенная карта ФАП; маршрут работает без ключа
    yandex_maps_api_key: str = _env("YANDEX_MAPS_API_KEY")

    # Почта для дублирующих писем с напоминаниями
    smtp_host: str = _env("SMTP_HOST", "smtp.yandex.ru")
    smtp_port: int = int(_env("SMTP_PORT", "465"))
    smtp_user: str = _env("SMTP_USER")
    smtp_password: str = _env("SMTP_PASSWORD")

    @property
    def is_dev(self) -> bool:
        return self.app_env == "dev"


settings = Settings()
