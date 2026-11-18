"""Общее для всех маршрутов: шаблоны и ответы для HTMX."""
from functools import lru_cache
from hashlib import sha256
from pathlib import Path

from fastapi.responses import Response
from fastapi.templating import Jinja2Templates

from app.config import BASE_DIR

@lru_cache(maxsize=4)
def _asset_hash(path: Path, _mtime_ns: int) -> str:
    return sha256(path.read_bytes()).hexdigest()[:12]


def _static_version(path: str) -> str:
    asset = BASE_DIR / "static" / path
    return _asset_hash(asset, asset.stat().st_mtime_ns)


templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.globals["static_version"] = _static_version


def htmx_redirect(url: str) -> Response:
    """HTMX сам перейдёт на страницу, увидев заголовок HX-Redirect."""
    return Response(status_code=204, headers={"HX-Redirect": url})
