"""Контракт API: поля рукописных TS-интерфейсов фронта против `components` OpenAPI.

Фронт описывает ответы и тела запросов руками (`frontend/src/shared/api/types.ts`,
`endpoints/*.ts`) и приводит ответ сервера через `body as T` — tsc не видит, что поля
нет в ответе (UI показывает «—»), а Pydantic с `extra=ignore` молча выбрасывает лишнее
поле тела запроса. Тест разбирает исходники TS (без сборки фронта) и сверяет каждый
интерфейс, у которого есть парная модель в OpenAPI приложения:
- пара находится по имени (`University` ↔ `University`/`UniversityOut`) или по таблице
  `EXPLICIT_PAIRS` для ключевых типов с другим именем (заявка, комментарий, тела запросов);
- поле интерфейса (с учётом `extends`) обязано быть в `properties` модели. Поля только
  на бэкенде допустимы: интерфейс может описывать часть ответа;
- известные расхождения — в `KNOWN_DRIFT`. Новое расхождение роняет тест; исправленное,
  но не убранное из списка — тоже (список только сокращается).

Сверяются имена полей верхнего уровня, не типы и не вложенные объекты.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from app.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]
API_DIR = REPO_ROOT / "frontend" / "src" / "shared" / "api"

# ключевые типы фронта, чьё имя не совпадает с моделью бэкенда: TS-интерфейс → схема
EXPLICIT_PAIRS: dict[str, str] = {
    # заявки
    "RequestItem": "RequestOut",
    "RequestClient": "ClientOut",
    "RequestComment": "CommentOut",
    "CreateRequestPayload": "RequestCreate",
    "UpdateRequestPayload": "RequestUpdate",
    "TransitionPayload": "TransitionBody",
    "PortfolioTransferPayload": "PortfolioTransferBody",
    "FileObject": "FileOut",
    # профиль и роли
    "CurrentUser": "MeResponse",
    "AccessInfo": "AccessOut",
    "RoleCreateBody": "RoleCreate",
    "RolePatchBody": "RolePatch",
    # справочники CRM
    "UniversityPayload": "UniversityCreate",
    "UniversityContact": "ContactOut",
    "UniversityContactPayload": "ContactCreate",
    "ContractPayload": "ContractCreate",
    "ProductPayload": "ProductCreate",
    "ProgramPayload": "ProgramCreate",
    "ItDirection": "DirectionOut",
    "ItDirectionPayload": "DirectionCreate",
    # пул талантов и активности
    "StudentPayload": "StudentCreate",
    "StudentStatusPayload": "StudentStatusBody",
    "StudentRevealResult": "StudentRevealOut",
    "ActivityScheduleEntry": "ScheduleSlotOut",
    # конструктор процессов
    "WorkflowStatusBody": "StatusSpec",
    "WorkflowTransitionBody": "TransitionSpec",
    "CreateChangePayload": "ChangeCreateBody",
    "WorkflowChangeRecord": "ChangeRequestOut",
    "WorkflowChangeImpact": "ImpactOut",
    "WorkflowMigrationReport": "MigrationReport",
    "WorkflowMigrationTimings": "MigrationTimings",
    # отчёты и выгрузки
    "ReportDynamicsPoint": "DynamicsPoint",
    "ReportDynamicsSeries": "DynamicsSeries",
    "KamActivityDynamicsSeries": "DynamicsSeries",
    "KamActivityEnvelopeParams": "KamActivityParamsOut",
    "KamActivityKam": "KamActivityKamRow",
    "KamActivityUniversity": "KamActivityUniversityRow",
    "KamActivityRequest": "KamActivityRequestRow",
    "KamActivityRequestStage": "RequestStage",
    "KamActivityStageChip": "StageChip",
    "InteractionsParams": "InteractionsParamsOut",
    "SlaPreviousPeriod": "PreviousPeriod",
    "CreateExportJobPayload": "ExportJobBody",
    "PutMappingPayload": "MappingBody",
    "CreatePresetPayload": "PresetIn",
    # уведомления
    "NotificationSettings": "SettingsIn",
}

# известные расхождения на момент введения теста: TS-интерфейс → поля, которых нет в схеме.
# Исправили — уберите запись (тест напомнит); новых сюда не добавлять без причины.
# Уже исправлены до введения теста: Contract.university/counterparty, RequestComment.deleted_at,
# University.*_count (aggregates), CreateRequestPayload.client/client_kind (B2C с доски).
KNOWN_DRIFT: dict[str, frozenset[str]] = {
    # ActivityOut отдаёт university_id без подписи вуза
    "Activity": frozenset({"university"}),
    # FileOut без имени загрузившего (подпись в списке файлов пустая)
    "FileObject": frozenset({"uploaded_by_name"}),
    # выбранные ИТ-направления в конверт отчёта «Взаимодействия» не попадают
    "InteractionsReport": frozenset({"directions"}),
    # цвет серии назначает фронт (поле необязательное)
    "ReportDynamicsSeries": frozenset({"color"}),
    # денормализованные подписи связей «если backend их отдаёт» — не отдаёт
    "RequestItem": frozenset({"product", "program", "university"}),
    # approved_by есть только в ответе approve (ApplyResultOut), в карточке пакета — decided_by
    "WorkflowChangeRecord": frozenset({"approved_by"}),
    # add_status не принимает is_initial (новый этап всегда не начальный, см. workflow/model.ts)
    "WorkflowStatusBody": frozenset({"is_initial"}),
}

_INTERFACE = re.compile(
    r"^export interface (\w+)(?:<[^>{]*>)?(?: extends ([^{]+))?\s*\{\n(.*?)^\}", re.M | re.S
)
_FIELD = re.compile(r"^  (\w+)\??:", re.M)  # поля верхнего уровня (отступ 2 пробела)


@dataclass(frozen=True)
class TsInterface:
    name: str
    file: str
    extends: tuple[str, ...]
    fields: frozenset[str]


def _parse_interfaces() -> dict[str, TsInterface]:
    files = [API_DIR / "types.ts", *sorted((API_DIR / "endpoints").glob("*.ts"))]
    result: dict[str, TsInterface] = {}
    for path in files:
        for match in _INTERFACE.finditer(path.read_text(encoding="utf-8")):
            name, extends, body = match.groups()
            bases = tuple(
                re.sub(r"<.*", "", base).strip()
                for base in re.split(r",\s*(?![^<]*>)", extends or "")
                if base.strip()
            )
            result[name] = TsInterface(
                name=name,
                file=str(path.relative_to(REPO_ROOT)),
                extends=bases,
                fields=frozenset(_FIELD.findall(body)),
            )
    return result


def _all_fields(interfaces: dict[str, TsInterface], name: str, seen: frozenset[str]) -> set[str]:
    iface = interfaces[name]
    fields = set(iface.fields)
    for base in iface.extends:
        if base in interfaces and base not in seen:
            fields |= _all_fields(interfaces, base, seen | {name})
    return fields


@pytest.fixture(scope="module")
def components() -> dict[str, Any]:
    return create_app().openapi()["components"]["schemas"]


@pytest.fixture(scope="module")
def interfaces() -> dict[str, TsInterface]:
    parsed = _parse_interfaces()
    assert len(parsed) > 100, f"разобрано подозрительно мало TS-интерфейсов: {len(parsed)}"
    return parsed


@pytest.fixture(scope="module")
def pairs(interfaces: dict[str, TsInterface], components: dict[str, Any]) -> dict[str, str]:
    """TS-интерфейс → схема OpenAPI с `properties`."""
    result: dict[str, str] = {}
    for name in interfaces:
        candidates = [EXPLICIT_PAIRS[name]] if name in EXPLICIT_PAIRS else [name, f"{name}Out"]
        schema = next((c for c in candidates if "properties" in components.get(c, {})), None)
        if schema is not None:
            result[name] = schema
    return result


@pytest.fixture(scope="module")
def drift(
    interfaces: dict[str, TsInterface], components: dict[str, Any], pairs: dict[str, str]
) -> dict[str, set[str]]:
    """TS-интерфейс → поля, которых нет в парной схеме."""
    result: dict[str, set[str]] = {}
    for name, schema in pairs.items():
        missing = _all_fields(interfaces, name, frozenset()) - set(components[schema]["properties"])
        if missing:
            result[name] = missing
    return result


def test_explicit_pairs_exist(interfaces: dict[str, TsInterface], components: dict[str, Any]):
    stale = {
        ts: schema
        for ts, schema in EXPLICIT_PAIRS.items()
        if ts not in interfaces or "properties" not in components.get(schema, {})
    }
    assert not stale, (
        "EXPLICIT_PAIRS ссылается на TS-интерфейс или схему OpenAPI, которых больше нет "
        f"(переименовали? поправьте таблицу): {stale}"
    )


def test_key_interfaces_are_checked(pairs: dict[str, str]) -> None:
    key = {"University", "Contract", "Product", "Program", "Activity", "RequestItem"}
    assert key <= set(pairs), f"ключевые интерфейсы без парной схемы: {sorted(key - set(pairs))}"
    assert len(pairs) >= 100, f"сверяется подозрительно мало пар: {len(pairs)}"


def test_no_new_field_drift(
    interfaces: dict[str, TsInterface], pairs: dict[str, str], drift: dict[str, set[str]]
) -> None:
    new = {
        name: sorted(missing - KNOWN_DRIFT.get(name, frozenset()))
        for name, missing in drift.items()
        if missing - KNOWN_DRIFT.get(name, frozenset())
    }
    lines = [
        f"{interfaces[name].file}: {name} → {pairs[name]}: нет в OpenAPI {fields}"
        for name, fields in sorted(new.items())
    ]
    assert not new, (
        "поля TS-интерфейсов, которых нет в схеме бэкенда (UI покажет «—» или сервер молча "
        "выбросит поле тела) — поправьте тип или модель:\n" + "\n".join(lines)
    )


def test_known_drift_is_still_drift(drift: dict[str, set[str]]) -> None:
    fixed = {
        name: sorted(fields - drift.get(name, set()))
        for name, fields in KNOWN_DRIFT.items()
        if fields - drift.get(name, set())
    }
    assert not fixed, (
        f"расхождения устранены — уберите их из KNOWN_DRIFT в {Path(__file__).name}: {fixed}"
    )
