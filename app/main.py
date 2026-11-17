"""Точка входа: создаёт приложение и подключает разделы.

Запуск: uvicorn app.main:app --reload
"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import BASE_DIR
from app.routers import auth, cabinet, patient

app = FastAPI(title="Пульс")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

app.include_router(auth.router)
app.include_router(patient.router)
app.include_router(cabinet.router)
