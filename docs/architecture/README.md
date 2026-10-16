# Архитектура «Альмы»: Archi и перечень компонентов

| Что | Где | Как обновить |
|---|---|---|
| Функциональная и компонентная архитектура в Archi: CSV для импорта, модель `alma.archimate` с двумя видами, превью в Mermaid | [`archi/`](archi/README.md) | `python3 docs/architecture/archi/build_archi.py` |
| Перечень всех библиотек и компонентов: Python, npm, образы контейнеров, шрифты, действия CI — с версиями и лицензиями | [`libraries.md`](libraries.md) | `cd backend && .venv/bin/python -m app.tools.list_libraries` |

Оба набора файлов собираются генераторами из репозитория: модель Archi — из данных
`archi/build_archi.py` и каталога прав `backend/app/core/rbac_catalog.py`, перечень — из
`uv.lock`, `frontend/package-lock.json`, Dockerfile и манифестов `deploy/`. Вручную их не
правят. Тест `backend/tests/test_architecture_docs.py` проверяет, что модель актуальна и
совпадает с кодом (пути API, права, экраны, каталоги), а в перечне есть каждый пакет
из lock-файлов и каждый сторонний образ.

Подробные проектные документы с диаграммами C4, ERD и топологией развёртывания —
в [`docs/design/`](../design/OVERVIEW.md).
