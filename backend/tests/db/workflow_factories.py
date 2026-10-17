"""Фабрики доработки F3 (массовый перенос заявок при удалении этапа).

Общие фабрики (пользователи, вузы, правила, личные настройки) — tests/db/factories.py.
Здесь — пакет «удалить «Первичный контакт» с переносом в «Новая»» и массовая вставка
заявок одним INSERT … SELECT generate_series: те же функции, что у инструмента замера
(app/tools/bench_migration.py), чтобы тесты и замер мерили один сценарий.
"""

from __future__ import annotations

from app.tools.bench_migration import (
    bulk_insert_open_deals,
    delete_first_contact_ops,
    raw_operations,
)

__all__ = ["bulk_insert_open_deals", "delete_first_contact_ops", "raw_operations"]
