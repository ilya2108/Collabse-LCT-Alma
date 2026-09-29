"""Пагинация, сортировка и конверт списков (api-contract.md §1.5)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Query
from sqlalchemy import Select
from sqlalchemy.orm import InstrumentedAttribute

from app.core.errors import validation_error


@dataclass(frozen=True)
class ListParams:
    limit: int
    offset: int
    sort: str | None


def list_params(
    limit: int = Query(25, ge=1, le=200, description="Размер страницы"),
    offset: int = Query(0, ge=0, description="Смещение"),
    sort: str | None = Query(
        None,
        description="Поля сортировки через запятую; префикс «-» — по убыванию",
    ),
) -> ListParams:
    return ListParams(limit=limit, offset=offset, sort=sort)


def list_envelope(items: list[Any], total: int, params: ListParams) -> dict[str, Any]:
    return {"items": items, "total": total, "limit": params.limit, "offset": params.offset}


def apply_sort(
    stmt: Select,
    sort: str | None,
    allowed: dict[str, InstrumentedAttribute],
    default: str,
) -> Select:
    """Применяет `sort=-field1,field2` к запросу; неизвестное поле → 400 validation_error."""
    expr = sort or default
    clauses = []
    for token in (t.strip() for t in expr.split(",")):
        if not token:
            continue
        descending = token.startswith("-")
        field = token[1:] if descending else token
        column = allowed.get(field)
        if column is None:
            raise validation_error(
                "Ошибка валидации данных",
                details=[
                    {
                        "field": "sort",
                        "code": "unknown_sort_field",
                        "message": (
                            f"Недопустимое поле сортировки «{field}». "
                            f"Доступные: {', '.join(sorted(allowed))}"
                        ),
                    }
                ],
            )
        clauses.append(column.desc() if descending else column.asc())
    return stmt.order_by(*clauses) if clauses else stmt
