"""Точка входа: создаёт приложение и подключает разделы.

Запуск: uvicorn app.main:app --reload
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import models
from app.access import AccessDenied, AuthenticationRequired
from app.config import BASE_DIR, settings
from app.database import Base, SessionLocal, engine, migrate_schema
from app.routers import admin, auth, cabinet, patient
from app.services.demo import seed_demo_data


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    migrate_schema()
    if settings.is_dev:
        with SessionLocal() as db:
            seed_demo_data(db)
    yield


app = FastAPI(title="Пульс", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="pulse_session",
    https_only=settings.app_env == "prod",
    same_site="lax",
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.exception_handler(AuthenticationRequired)
def authentication_required(request: Request, _: AuthenticationRequired):
    if request.headers.get("HX-Request") == "true":
        return Response(status_code=204, headers={"HX-Redirect": "/login"})
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(AccessDenied)
def access_denied(_: Request, __: AccessDenied):
    return PlainTextResponse("У вашей учётной записи нет доступа к этому разделу", status_code=403)


app.include_router(auth.router)
app.include_router(patient.router)
app.include_router(cabinet.router)
app.include_router(admin.router)
