"""Выбор ближайшего медпункта и ссылки для связи и карты."""
import re
from math import cos, radians, sin
from urllib.parse import urlencode

from app.models import Fap

# Проверяем адрес и точку независимо: замена названия или части полей
# не превращает старый пример в проверенную запись.
_DEMO_FAP_ADDRESSES = {
    ("пестречи", "ул. садовая, д. 18"),
    ("карадуган", "ул. центральная, д. 4"),
}
_DEMO_FAP_POINTS = {(55.75, 49.65), (55.68, 49.72)}


def has_demo_fap_data(
    village: str | None, address: str | None, latitude: float | None, longitude: float | None,
) -> bool:
    address_key = ((village or "").strip().casefold(), (address or "").strip().casefold())
    return address_key in _DEMO_FAP_ADDRESSES or (latitude, longitude) in _DEMO_FAP_POINTS


def is_demo_fap(fap: Fap) -> bool:
    return has_demo_fap_data(fap.village, fap.address, fap.latitude, fap.longitude)


def coordinates(latitude: float | None, longitude: float | None) -> tuple[float, float] | None:
    if latitude is None or longitude is None:
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    if (latitude, longitude) == (0, 0):
        return None
    return latitude, longitude


def fap_coordinates(fap: Fap) -> tuple[float, float] | None:
    return None if is_demo_fap(fap) else coordinates(fap.latitude, fap.longitude)


def nearest_fap(faps: list[Fap], latitude: float, longitude: float) -> Fap | None:
    if coordinates(latitude, longitude) is None:
        return None
    lat, lon = radians(latitude), radians(longitude)
    nearest, best = None, float("inf")
    for fap in faps:
        point = fap_coordinates(fap)
        if point is None:
            continue
        fap_lat, fap_lon = map(radians, point)
        # Сравниваем расстояния по прямой по формуле гаверсинуса.
        distance = sin((fap_lat - lat) / 2)**2 + cos(lat) * cos(fap_lat) * sin((fap_lon - lon) / 2)**2
        if distance < best:
            nearest, best = fap, distance
    return nearest


def phone_href(phone: str) -> str:
    number = re.sub(r"[\s().-]", "", phone)
    if not re.fullmatch(r"(?:\+7|8)[0-9]{10}", number):
        return ""
    if re.fullmatch(r"(?:\+7|8)80000000[0-9]{2}", number):
        return ""
    return f"tel:{number}"


def fap_map_link(fap: Fap) -> str:
    point = fap_coordinates(fap)
    if point:
        latitude, longitude = point
        # Пункт отправления пациент задаёт в Яндекс Картах.
        params = {"mode": "routes", "rtext": f"~{latitude},{longitude}", "rtt": "auto"}
    else:
        params = {"text": f"{fap.village}, {fap.address}"}
    return "https://yandex.ru/maps/?" + urlencode(params)
