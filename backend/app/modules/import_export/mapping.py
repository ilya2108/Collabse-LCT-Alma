"""Интерактивный маппинг колонок импорта (api-contract.md §6.2–6.3).

Реестр целевых полей по `entity_type`, словарь синонимов русских заголовков для
`suggested_mapping` (Р-17: словарь пополняется без изменения схемы), трансформации
значений и применение маппинга к строке. Модуль чистый (без БД) — юнит-тестируется.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.core.errors import validation_error

TRANSFORMS = ("trim", "lowercase", "uppercase", "date_ru", "phone_normalize")

_DATE_RU_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")
_NORMALIZE_HEADER_RE = re.compile(r"[\s_\-./()№«»\"']+")


@dataclass(frozen=True)
class TargetField:
    field: str
    label: str
    required: bool = False
    synonyms: tuple[str, ...] = ()
    lookup: str | None = None  # подсказка UI: by_name_or_inn / by_code_or_name / ...
    pii: str | None = None  # full_name | email | phone — маскирование ошибок и сэмплов


@dataclass(frozen=True)
class MappingEntry:
    """Строка маппинга из PUT /import/sessions/{id}/mapping."""

    source_column: int
    target_field: str
    transform: str | None = None
    lookup: str | None = None
    create_missing: bool = False
    # словарь замен значений («учится» → studying); ключи сравниваются без регистра
    value_map: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EntityFields:
    """Целевые поля одного entity_type + дефолтные ключи дедупликации."""

    entity_type: str
    label: str
    fields: tuple[TargetField, ...]
    default_match_by: tuple[str, ...] = ()

    def by_name(self) -> dict[str, TargetField]:
        return {f.field: f for f in self.fields}

    def required_fields(self) -> tuple[str, ...]:
        return tuple(f.field for f in self.fields if f.required)

    def pii_fields(self) -> dict[str, str]:
        return {f.field: f.pii for f in self.fields if f.pii}


# --------------------------------------------------------------------------------------
# Реестр целевых полей (стадия B3: universities, students, requests_b2b/b2c, dictionary:*)
# --------------------------------------------------------------------------------------

_FIO_SYNONYMS = ("фио", "ф и о", "fio", "фамилия имя отчество", "полное имя", "full name")
_EMAIL_SYNONYMS = ("email", "e mail", "почта", "эл почта", "электронная почта", "mail")
_PHONE_SYNONYMS = ("телефон", "тел", "phone", "номер телефона", "моб телефон")
_UNIVERSITY_SYNONYMS = ("вуз", "университет", "учебное заведение", "организация", "university")
_PROGRAM_SYNONYMS = ("программа", "курс", "program", "направление подготовки")
_PRODUCT_SYNONYMS = ("продукт", "направление", "услуга", "product")

STUDENT_FIELDS = EntityFields(
    entity_type="students",
    label="Студенты (Talent Pool)",
    fields=(
        TargetField("full_name", "ФИО", required=True, synonyms=_FIO_SYNONYMS, pii="full_name"),
        TargetField("email", "Email", required=True, synonyms=_EMAIL_SYNONYMS, pii="email"),
        TargetField("phone", "Телефон", synonyms=_PHONE_SYNONYMS, pii="phone"),
        TargetField(
            "university",
            "Вуз (название или ИНН)",
            synonyms=_UNIVERSITY_SYNONYMS,
            lookup="by_name_or_inn",
        ),
        TargetField(
            "program", "Программа (название)", synonyms=_PROGRAM_SYNONYMS, lookup="by_name"
        ),
        TargetField(
            "federal_project",
            "Федеральный проект",
            synonyms=("федпроект", "федеральный проект", "фед проект", "проект"),
            lookup="by_name",
        ),
        TargetField(
            "funnel_status",
            "Статус воронки",
            synonyms=("статус", "статус воронки", "воронка", "этап"),
        ),
        TargetField("notes", "Комментарий", synonyms=("комментарий", "заметки", "примечание")),
    ),
    default_match_by=("email",),
)

UNIVERSITY_FIELDS = EntityFields(
    entity_type="universities",
    label="Вузы",
    fields=(
        TargetField(
            "name",
            "Название",
            required=True,
            synonyms=("название", "наименование", "вуз", "полное наименование", "name"),
        ),
        TargetField(
            "short_name",
            "Краткое название",
            synonyms=("краткое название", "сокращение", "аббревиатура", "short name"),
        ),
        TargetField("inn", "ИНН", synonyms=("инн", "inn")),
        TargetField("kpp", "КПП", synonyms=("кпп", "kpp")),
        TargetField("region", "Регион", synonyms=("регион", "область", "субъект", "region")),
        TargetField("city", "Город", synonyms=("город", "нас пункт", "city")),
        TargetField("website", "Сайт", synonyms=("сайт", "web", "website", "url")),
        TargetField("notes", "Заметки", synonyms=("комментарий", "заметки", "примечание")),
    ),
    default_match_by=("inn", "name"),
)

REQUEST_B2B_FIELDS = EntityFields(
    entity_type="requests_b2b",
    label="Заявки B2B",
    fields=(
        TargetField(
            "title", "Название заявки", synonyms=("название", "заявка", "тема", "title")
        ),
        TargetField(
            "university",
            "Вуз (название или ИНН)",
            required=True,
            synonyms=_UNIVERSITY_SYNONYMS,
            lookup="by_name_or_inn",
        ),
        TargetField(
            "product",
            "Продукт (код или название)",
            synonyms=_PRODUCT_SYNONYMS,
            lookup="by_code_or_name",
        ),
        TargetField(
            "program", "Программа (название)", synonyms=_PROGRAM_SYNONYMS, lookup="by_name"
        ),
        TargetField(
            "assignee",
            "Ответственный (логин или email)",
            synonyms=("ответственный", "кам", "менеджер", "kam"),
            lookup="by_username_or_email",
        ),
        TargetField("amount", "Сумма", synonyms=("сумма", "бюджет", "стоимость", "amount")),
        TargetField(
            "description", "Описание", synonyms=("описание", "комментарий", "примечание")
        ),
    ),
)

REQUEST_B2C_FIELDS = EntityFields(
    entity_type="requests_b2c",
    label="Заявки B2C",
    fields=(
        TargetField(
            "client_kind",
            "Тип клиента (физлицо/юрлицо)",
            synonyms=("тип клиента", "тип", "клиент"),
        ),
        TargetField("full_name", "ФИО (физлицо)", synonyms=_FIO_SYNONYMS, pii="full_name"),
        TargetField("email", "Email", required=True, synonyms=_EMAIL_SYNONYMS, pii="email"),
        TargetField("phone", "Телефон", synonyms=_PHONE_SYNONYMS, pii="phone"),
        TargetField(
            "company_name",
            "Название организации (юрлицо)",
            synonyms=("организация", "компания", "юрлицо", "наименование организации"),
        ),
        TargetField("inn", "ИНН (юрлицо)", synonyms=("инн", "inn")),
        TargetField(
            "interaction_type",
            "Тип взаимодействия",
            synonyms=("тип взаимодействия", "взаимодействие", "формат"),
            lookup="by_code_or_name",
        ),
        TargetField(
            "product",
            "Продукт (код или название)",
            synonyms=_PRODUCT_SYNONYMS,
            lookup="by_code_or_name",
        ),
        TargetField(
            "program", "Программа (название)", synonyms=_PROGRAM_SYNONYMS, lookup="by_name"
        ),
        TargetField("title", "Название заявки", synonyms=("название", "заявка", "тема")),
        TargetField("amount", "Сумма", synonyms=("сумма", "стоимость", "amount")),
        TargetField(
            "description", "Описание", synonyms=("описание", "комментарий", "примечание")
        ),
    ),
    default_match_by=("email",),
)

_DICTIONARY_FIELDS: dict[str, EntityFields] = {
    "federal_projects": EntityFields(
        entity_type="dictionary:federal_projects",
        label="Справочник «Федеральные проекты»",
        fields=(
            TargetField(
                "name", "Название", required=True, synonyms=("название", "наименование", "проект")
            ),
            TargetField("code", "Код", synonyms=("код", "code")),
            TargetField("description", "Описание", synonyms=("описание", "комментарий")),
            TargetField("starts_on", "Дата начала", synonyms=("дата начала", "начало", "старт")),
            TargetField(
                "ends_on", "Дата окончания", synonyms=("дата окончания", "окончание", "финиш")
            ),
        ),
        default_match_by=("name",),
    ),
    "interaction_types": EntityFields(
        entity_type="dictionary:interaction_types",
        label="Справочник «Типы взаимодействия B2C»",
        fields=(
            TargetField("code", "Код", required=True, synonyms=("код", "code")),
            TargetField(
                "name", "Название", required=True, synonyms=("название", "наименование")
            ),
            TargetField("description", "Описание", synonyms=("описание", "комментарий")),
        ),
        default_match_by=("code",),
    ),
    # справочник продуктов (файл организаторов «Вендоры.xlsx»): продукт + вендор.
    # Контакты (ФИО/телефон/почта) — ПДн, в справочник продуктов НЕ импортируются:
    # соответствующих целевых полей нет, колонки остаются незамапленными.
    "products": EntityFields(
        entity_type="dictionary:products",
        label="Справочник «Продукты»",
        fields=(
            TargetField(
                "name",
                "Продукт",
                required=True,
                synonyms=("продукт", "продукты", "название", "наименование", "product"),
            ),
            TargetField(
                "vendor",
                "Вендор (компания)",
                synonyms=(
                    "компания",
                    "вендор",
                    "производитель",
                    "поставщик",
                    "company",
                    "vendor",
                ),
            ),
        ),
        default_match_by=("name",),
    ),
    "universities_registry": EntityFields(
        entity_type="dictionary:universities_registry",
        label="Предзагрузка реестра вузов РФ",
        fields=UNIVERSITY_FIELDS.fields,
        default_match_by=("inn", "name"),
    ),
}

# справочники-списки значений без собственных таблиц: живут в app_setting['dict:{name}']
VALUE_LIST_DICTIONARIES = ("regions", "request_sources", "activity_kinds", "tags")

_VALUE_LIST_FIELDS = (
    TargetField("name", "Значение", required=True, synonyms=("название", "значение", "name")),
    TargetField("code", "Код", synonyms=("код", "code")),
)

for _name in VALUE_LIST_DICTIONARIES:
    _DICTIONARY_FIELDS[_name] = EntityFields(
        entity_type=f"dictionary:{_name}",
        label=f"Справочник «{_name}»",
        fields=_VALUE_LIST_FIELDS,
        default_match_by=("name",),
    )

_ENTITY_REGISTRY: dict[str, EntityFields] = {
    "students": STUDENT_FIELDS,
    "universities": UNIVERSITY_FIELDS,
    "requests_b2b": REQUEST_B2B_FIELDS,
    "requests_b2c": REQUEST_B2C_FIELDS,
}


def supported_entity_types() -> list[str]:
    return sorted(_ENTITY_REGISTRY) + [f"dictionary:{n}" for n in sorted(_DICTIONARY_FIELDS)]


def entity_fields(entity_type: str) -> EntityFields:
    """Реестр полей по entity_type; неизвестный тип → 400 validation_error."""
    if entity_type.startswith("dictionary:"):
        spec = _DICTIONARY_FIELDS.get(entity_type.split(":", 1)[1])
    else:
        spec = _ENTITY_REGISTRY.get(entity_type)
    if spec is None:
        raise validation_error(
            "Ошибка валидации данных",
            details=[
                {
                    "field": "entity_type",
                    "code": "unsupported_entity_type",
                    "message": (
                        f"Импорт «{entity_type}» не поддерживается. Доступно: "
                        + ", ".join(supported_entity_types())
                    ),
                }
            ],
        )
    return spec


# --------------------------------------------------------------------------------------
# Автопредложение маппинга по словарю синонимов
# --------------------------------------------------------------------------------------


def normalize_header(header: str) -> str:
    value = _NORMALIZE_HEADER_RE.sub(" ", header.strip().lower().replace("ё", "е"))
    return value.strip()


def suggest_mapping(columns: list[str], spec: EntityFields) -> list[dict[str, Any]]:
    """`suggested_mapping` (§6.2): точное совпадение синонима 0.95, вхождение 0.8."""
    suggestions: list[dict[str, Any]] = []
    used_fields: set[str] = set()
    for index, column in enumerate(columns):
        header = normalize_header(column)
        if not header:
            continue
        best: tuple[float, str] | None = None
        for target in spec.fields:
            if target.field in used_fields:
                continue
            variants = (normalize_header(target.label),) + tuple(
                normalize_header(s) for s in target.synonyms
            )
            for synonym in variants:
                if not synonym:
                    continue
                if header == synonym:
                    confidence = 0.95
                elif synonym in header or header in synonym:
                    confidence = 0.8
                else:
                    continue
                if best is None or confidence > best[0]:
                    best = (confidence, target.field)
        if best is not None:
            confidence, target_field = best
            used_fields.add(target_field)
            suggestions.append(
                {"source_column": index, "target_field": target_field, "confidence": confidence}
            )
    return suggestions


# --------------------------------------------------------------------------------------
# Трансформации и применение маппинга
# --------------------------------------------------------------------------------------


def normalize_phone(raw: str) -> str:
    """`8 (916) 000-11-22` → `+79160001122`; иностранные номера — как есть с `+`."""
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return raw.strip()
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    if len(digits) == 10:
        digits = "7" + digits
    return f"+{digits}"


def date_ru_to_iso(raw: str) -> str:
    """`дд.мм.гггг` → ISO; уже-ISO значения пропускаются как есть."""
    value = raw.strip()
    match = _DATE_RU_RE.match(value)
    if match:
        day, month, year = (int(g) for g in match.groups())
        return date(year, month, day).isoformat()
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError as exc:
        raise ValueError(f"Дата «{raw}» не в формате дд.мм.гггг") from exc


def apply_transform(value: str, transform: str) -> str:
    if transform == "trim":
        return value.strip()
    if transform == "lowercase":
        return value.strip().lower()
    if transform == "uppercase":
        return value.strip().upper()
    if transform == "date_ru":
        return date_ru_to_iso(value)
    if transform == "phone_normalize":
        return normalize_phone(value)
    raise ValueError(f"Неизвестная трансформация «{transform}»")


def parse_mapping_entries(raw_mapping: list[dict[str, Any]]) -> list[MappingEntry]:
    entries: list[MappingEntry] = []
    for item in raw_mapping:
        raw_value_map = item.get("value_map") or {}
        entries.append(
            MappingEntry(
                source_column=int(item["source_column"]),
                target_field=str(item["target_field"]),
                transform=item.get("transform"),
                lookup=item.get("lookup"),
                create_missing=bool(item.get("create_missing", False)),
                value_map={str(k).strip().lower(): str(v) for k, v in raw_value_map.items()},
            )
        )
    return entries


def validate_mapping(
    entries: list[MappingEntry],
    columns_count: int,
    spec: EntityFields,
) -> None:
    """Проверка маппинга при сохранении: поля, колонки, трансформации, required."""
    details: list[dict[str, Any]] = []
    known = spec.by_name()
    seen_targets: set[str] = set()
    for i, entry in enumerate(entries):
        prefix = f"mapping.{i}"
        if entry.target_field not in known:
            details.append(
                {
                    "field": f"{prefix}.target_field",
                    "code": "unknown_target_field",
                    "message": f"Неизвестное целевое поле «{entry.target_field}»",
                }
            )
        elif entry.target_field in seen_targets:
            details.append(
                {
                    "field": f"{prefix}.target_field",
                    "code": "duplicate_target_field",
                    "message": f"Поле «{entry.target_field}» замаплено дважды",
                }
            )
        else:
            seen_targets.add(entry.target_field)
        if not 0 <= entry.source_column < columns_count:
            details.append(
                {
                    "field": f"{prefix}.source_column",
                    "code": "column_out_of_range",
                    "message": f"Колонки №{entry.source_column} нет в файле",
                }
            )
        if entry.transform is not None and entry.transform not in TRANSFORMS:
            details.append(
                {
                    "field": f"{prefix}.transform",
                    "code": "unknown_transform",
                    "message": (
                        f"Неизвестная трансформация «{entry.transform}». "
                        f"Доступно: {', '.join(TRANSFORMS)}"
                    ),
                }
            )
    for required in spec.required_fields():
        if required not in seen_targets:
            details.append(
                {
                    "field": "mapping",
                    "code": "required_field_missing",
                    "message": f"Обязательное поле «{known[required].label}» не замаплено",
                }
            )
    if details:
        raise validation_error("Ошибка валидации данных", details)


def map_row(
    row: list[str | None],
    entries: list[MappingEntry],
    default_values: dict[str, Any] | None = None,
) -> tuple[dict[str, str | None], list[dict[str, Any]]]:
    """Строка файла → dict целевых полей; ошибки трансформаций — построчно."""
    mapped: dict[str, str | None] = {}
    errors: list[dict[str, Any]] = []
    for entry in entries:
        value = row[entry.source_column] if entry.source_column < len(row) else None
        if value is not None and entry.transform:
            try:
                value = apply_transform(value, entry.transform)
            except ValueError as exc:
                errors.append(
                    {
                        "column": entry.target_field,
                        "code": "transform_error",
                        "message": str(exc),
                    }
                )
                value = None
        if value is not None and entry.value_map:
            value = entry.value_map.get(value.strip().lower(), value)
        mapped[entry.target_field] = value
    for target_field, default in (default_values or {}).items():
        if mapped.get(target_field) in (None, ""):
            mapped[target_field] = str(default) if default is not None else None
    return mapped, errors
