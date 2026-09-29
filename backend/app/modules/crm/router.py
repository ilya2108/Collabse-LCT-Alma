"""Сборный роутер модуля crm: вузы, контакты, договоры, продукты, программы."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.crm.contracts import router as contracts_router
from app.modules.crm.products import router as products_router
from app.modules.crm.programs import router as programs_router
from app.modules.crm.universities import router as universities_router

router = APIRouter()
router.include_router(universities_router)
router.include_router(contracts_router)
router.include_router(products_router)
router.include_router(programs_router)
