"""Общее для всех маршрутов: шаблоны и ответы для HTMX."""
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates

from app.config import BASE_DIR

templates = Jinja2Templates(directory=BASE_DIR / "templates")


def htmx_redirect(url: str) -> Response:
    """HTMX сам перейдёт на страницу, увидев заголовок HX-Redirect."""
    return Response(status_code=204, headers={"HX-Redirect": url})
