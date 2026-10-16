"""Каталог прав и системных ролей — единственное место, где права определяются кодом.

Решения F2 (роли и права в БД Альмы):
- права (`app_permission`) создаёт только программист: право без проверки в коде ничего
  не значит. Миграция 0004 засевает литеральную копию этого каталога, интеграционный тест
  сверяет БД с кодом; `modules/access/bootstrap.py` — идемпотентный upsert из кода;
- право «с областью» (`scoped`) действует только в процессах роли (B2B/B2C) и с её
  областью записей (все / только свои); остальные права общие (справочники, админка);
- `requires` — зависимости: редактор ролей не даст сохранить право без того, без чего
  оно бессмысленно (правка без просмотра и т. п.); наборы системных ролей замкнуты;
- наборы системных ролей подобраны так, что доступ 4 базовых ролей к каждому эндпоинту
  совпадает с прежней матрицей доступа по ролям (регрессия — tests/test_access_matrix.py).

Модуль не зависит от БД и FastAPI: его импортируют миграционные тесты, фронтовые контракты
(`/admin/permissions`) и чистое ядро `core/access.py`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

WORKFLOW_TYPES: tuple[str, ...] = ("b2b", "b2c")
WORKFLOW_TYPE_LABELS: dict[str, str] = {"b2b": "B2B", "b2c": "B2C"}
RECORD_SCOPES: tuple[str, ...] = ("all", "own")

# код роли неизменяем: он хранится в app_user.roles[] и workflow_transition.allowed_roles
ROLE_CODE_PATTERN = r"^[a-z][a-z0-9_]{1,39}$"
# служебные роли Keycloak (сервисные клиенты и роли realm по умолчанию) — не роли CRM
RESERVED_ROLE_CODES = frozenset(
    {"svc_notification", "svc_integration", "offline_access", "uma_authorization"}
)


@dataclass(frozen=True)
class PermissionDef:
    code: str
    group: str
    name: str
    description: str
    scoped: bool = False
    requires: tuple[str, ...] = ()


@dataclass(frozen=True)
class SystemRoleDef:
    code: str
    name: str
    description: str
    permissions: frozenset[str]
    locked: bool = False


# (код группы, подпись) — порядок групп в редакторе роли и матрице прав
PERMISSION_GROUPS: tuple[tuple[str, str], ...] = (
    ("requests", "Заявки"),
    ("workflow", "Конструктор процессов"),
    ("crm", "Вузы, договоры, продукты, программы"),
    ("talent", "Пул талантов и активности"),
    ("reports", "Аналитика и экспорт"),
    ("import", "Импорт"),
    ("notifications", "Уведомления"),
    ("admin", "Администрирование"),
)

_R = ("requests:read",)

PERMISSIONS: tuple[PermissionDef, ...] = (
    # --- заявки (в процессах роли) -------------------------------------------------------
    PermissionDef(
        "requests:read",
        "requests",
        "Просмотр заявок, доски и карточек",
        "Реестр и доска заявок, карточка, история и комментарии",
        scoped=True,
    ),
    PermissionDef(
        "requests:write",
        "requests",
        "Создание заявок и правка своих",
        "Новые заявки и изменение полей заявок, где пользователь — ответственный",
        scoped=True,
        requires=_R,
    ),
    PermissionDef(
        "requests:write_any",
        "requests",
        "Правка и переходы по чужим заявкам",
        "Изменение полей и перевод по этапам заявок любого ответственного",
        scoped=True,
        requires=("requests:read", "requests:write"),
    ),
    PermissionDef(
        "requests:transition",
        "requests",
        "Перевод заявок по этапам",
        "Переходы своих заявок (чужих — вместе с «Правка и переходы по чужим заявкам»)",
        scoped=True,
        requires=_R,
    ),
    PermissionDef(
        "requests:assign",
        "requests",
        "Назначение ответственного: взять заявку на себя",
        "Взять себе свободную заявку (её ответственный не ведёт портфель как КАМ)",
        scoped=True,
        requires=_R,
    ),
    PermissionDef(
        "requests:assign_any",
        "requests",
        "Назначение любого ответственного",
        "Назначение и переназначение ответственного по любой заявке, в том числе при создании",
        scoped=True,
        requires=("requests:read", "requests:assign"),
    ),
    PermissionDef(
        "requests:kam",
        "requests",
        "Ведёт портфель заявок как КАМ (отчёт нагрузки, «свободные» заявки)",
        "Пользователь попадает в отчёт нагрузки КАМов и в выбор КАМа на дашборде; "
        "его заявки не считаются свободными",
        scoped=True,
        requires=_R,
    ),
    PermissionDef(
        "requests:reopen",
        "requests",
        "Переоткрытие закрытых заявок",
        "Возврат заявки из терминального этапа в работу",
        scoped=True,
        requires=_R,
    ),
    PermissionDef(
        "requests:delete",
        "requests",
        "Удаление заявок (в архив)",
        "Мягкое удаление: заявка уходит в архив и пропадает из выдач",
        scoped=True,
        requires=("requests:read", "requests:write"),
    ),
    PermissionDef(
        "comments:write",
        "requests",
        "Комментарии к заявкам",
        "Добавление комментариев и удаление своих",
        scoped=True,
        requires=_R,
    ),
    PermissionDef(
        "comments:delete_any",
        "requests",
        "Удаление чужих комментариев",
        "Модерация переписки по заявке",
        scoped=True,
        requires=("requests:read", "comments:write"),
    ),
    PermissionDef(
        "requests:files",
        "requests",
        "Файлы заявок: загрузка и удаление своих",
        "Вложения карточки заявки; удаление чужих файлов — с правом "
        "«Правка и переходы по чужим заявкам»",
        scoped=True,
        requires=_R,
    ),
    PermissionDef(
        "clients:pii",
        "requests",
        "Контакты клиентов B2C без маскирования",
        "ФИО, email и телефон физлиц в карточке заявки показываются полностью",
        requires=_R,
    ),
    # --- конструктор процессов (в процессах роли) ----------------------------------------
    PermissionDef(
        "workflow:read",
        "workflow",
        "Просмотр схемы процесса",
        "Этапы, переходы и условия процесса в конструкторе",
        scoped=True,
    ),
    PermissionDef(
        "workflow:history",
        "workflow",
        "История и пакеты изменений схемы",
        "Журнал пакетов изменений и их статусы",
        scoped=True,
        requires=("workflow:read",),
    ),
    PermissionDef(
        "workflow:change",
        "workflow",
        "Правка схемы: черновик и пакет изменений",
        "Черновик, оценка влияния и отправка пакета на согласование",
        scoped=True,
        requires=("workflow:history",),
    ),
    PermissionDef(
        "workflow:approve",
        "workflow",
        "Согласование и публикация изменений схемы",
        "Согласование чужих пакетов; безопасный пакет автора применяется сразу",
        scoped=True,
        requires=("workflow:change",),
    ),
    # --- вузы, договоры, продукты, программы ---------------------------------------------
    PermissionDef(
        "universities:read",
        "crm",
        "Просмотр вузов и контактов",
        "Реестр вузов, карточка вуза и контактные лица",
    ),
    PermissionDef(
        "universities:write",
        "crm",
        "Создание и правка вузов и контактов",
        "Свои вузы (КАМ — ответственный) и контакты; чужие — с «Правкой чужих вузов и договоров»",
        requires=("universities:read",),
    ),
    PermissionDef(
        "universities:assign",
        "crm",
        "Назначение ответственного КАМа за вуз",
        "Смена КАМа вуза (в том числе при создании) и передача вузов в портфеле КАМа; "
        "без этого права КАМ заводит вуз только на себя",
        requires=("universities:write",),
    ),
    PermissionDef(
        "universities:delete",
        "crm",
        "Удаление вузов",
        "Деактивация вуза",
        requires=("universities:write",),
    ),
    PermissionDef(
        "contacts:delete",
        "crm",
        "Удаление контактов вуза",
        "Удаление контактных лиц из карточки вуза",
        requires=("universities:write",),
    ),
    PermissionDef(
        "contracts:read", "crm", "Просмотр договоров", "Реестр, карточка и файлы договоров"
    ),
    PermissionDef(
        "contracts:write",
        "crm",
        "Создание и правка договоров, загрузка файлов",
        "Свои договоры; чужие — с «Правкой чужих вузов и договоров»",
        requires=("contracts:read",),
    ),
    PermissionDef(
        "contracts:delete",
        "crm",
        "Удаление договоров",
        "Расторжение (мягкое удаление) договора",
        requires=("contracts:write",),
    ),
    PermissionDef(
        "contracts:files_delete",
        "crm",
        "Удаление файлов договоров",
        "Удаление вложений из карточки договора",
        requires=("contracts:write",),
    ),
    PermissionDef(
        "crm:write_any",
        "crm",
        "Правка чужих вузов и договоров (КАМ — другой)",
        "Без этого права правка доступна только там, где пользователь — ответственный",
        requires=("universities:read", "contracts:read"),
    ),
    PermissionDef("products:read", "crm", "Просмотр продуктов", "Каталог продуктов"),
    PermissionDef(
        "products:write",
        "crm",
        "Создание и правка продуктов",
        "Карточки продуктов каталога",
        requires=("products:read",),
    ),
    PermissionDef(
        "products:delete",
        "crm",
        "Удаление продуктов",
        "Деактивация продукта",
        requires=("products:write",),
    ),
    PermissionDef("programs:read", "crm", "Просмотр программ", "Программы и их приоритеты"),
    PermissionDef(
        "programs:write",
        "crm",
        "Создание и правка программ",
        "Карточки программ",
        requires=("programs:read",),
    ),
    PermissionDef(
        "programs:priority",
        "crm",
        "Ручная приоритизация программ",
        "Изменение порядка программ (меньше — выше)",
        requires=("programs:read",),
    ),
    PermissionDef(
        "programs:delete",
        "crm",
        "Удаление программ",
        "Деактивация программы",
        requires=("programs:write",),
    ),
    PermissionDef(
        "files:business",
        "crm",
        "Скачивание файлов договоров, студентов и заявок",
        "Без этого права файлы этих карточек не скачиваются",
    ),
    # --- пул талантов и активности -------------------------------------------------------
    PermissionDef(
        "students:read",
        "talent",
        "Просмотр студентов (ПДн маскированы)",
        "Реестр и карточка студента, история воронки",
    ),
    PermissionDef(
        "students:write",
        "talent",
        "Создание и правка студентов, смена статуса",
        "Карточка студента и переходы по воронке талантов",
        requires=("students:read",),
    ),
    PermissionDef(
        "students:reveal",
        "talent",
        "Раскрытие ПДн студента (с записью в аудит)",
        "Точечный просмотр ФИО и контактов; каждое раскрытие пишется в журнал аудита",
        requires=("students:read",),
    ),
    PermissionDef(
        "students:files",
        "talent",
        "Файлы студентов: просмотр и загрузка",
        "Вложения карточки студента",
        requires=("students:read",),
    ),
    PermissionDef(
        "students:files_delete",
        "talent",
        "Удаление файлов студентов",
        "Удаление вложений из карточки студента",
        requires=("students:files",),
    ),
    PermissionDef(
        "students:delete",
        "talent",
        "Удаление студентов",
        "Удаление карточки студента",
        requires=("students:write",),
    ),
    PermissionDef(
        "activities:read",
        "talent",
        "Просмотр активностей и расписания",
        "Сквозное расписание активностей",
    ),
    PermissionDef(
        "activities:write",
        "talent",
        "Создание и правка активностей",
        "Активности и их расписание",
        requires=("activities:read",),
    ),
    PermissionDef(
        "activities:participants",
        "talent",
        "Запись студентов на активности",
        "Добавление и удаление участников",
        requires=("activities:read",),
    ),
    # --- аналитика и экспорт -------------------------------------------------------------
    PermissionDef(
        "reports:read",
        "reports",
        "Дашборды и отчёты",
        "Без «Отчётов по всем КАМам» — только по заявкам, где пользователь ответственный",
        scoped=True,
    ),
    PermissionDef(
        "reports:all",
        "reports",
        "Отчёты по всем КАМам (иначе — только по своим заявкам)",
        "Агрегаты по всем заявкам процессов роли",
        scoped=True,
        requires=("reports:read",),
    ),
    PermissionDef(
        "export:run",
        "reports",
        "Экспорт данных (ПДн маскируются по правам)",
        "Выгрузки XLSX/CSV/JSON реестров и отчётов в пределах области роли",
    ),
    # --- импорт -------------------------------------------------------------------------
    PermissionDef(
        "import:run",
        "import",
        "Мастер импорта данных",
        "Загрузка файлов, маппинг, проверка и применение импорта",
    ),
    PermissionDef(
        "import:dictionaries",
        "import",
        "Импорт справочников через мастер",
        "Справочники в мастере импорта",
        requires=("import:run",),
    ),
    # --- уведомления --------------------------------------------------------------------
    PermissionDef(
        "notifications:self",
        "notifications",
        "Личные уведомления и привязка Telegram",
        "Лента, личные настройки каналов и событий, Telegram-бот",
    ),
    PermissionDef(
        "notification_rules:read",
        "notifications",
        "Просмотр правил уведомлений и порогов",
        "Правила адресации событий",
    ),
    PermissionDef(
        "notification_rules:write",
        "notifications",
        "Настройка правил уведомлений",
        "Создание, изменение и удаление правил",
        requires=("notification_rules:read",),
    ),
    # --- администрирование --------------------------------------------------------------
    PermissionDef("admin:users", "admin", "Список пользователей", "Пользователи и их роли"),
    PermissionDef(
        "admin:users_write",
        "admin",
        "Руководители для эскалаций, синхронизация с Keycloak",
        "Иерархия руководителей и перенос пользователей из Keycloak",
        requires=("admin:users",),
    ),
    PermissionDef(
        "admin:roles",
        "admin",
        "Роли и права, назначение ролей",
        "Создание и правка ролей, назначение ролей пользователям",
        requires=("admin:users",),
    ),
    PermissionDef(
        "admin:settings",
        "admin",
        "Системные настройки и пороги",
        "Пороги зависания, эскалации, режим выдачи файлов",
    ),
    PermissionDef(
        "admin:dictionaries",
        "admin",
        "Справочники",
        "Справочники системы, в том числе каталог ИТ-направлений",
    ),
    PermissionDef("admin:flags", "admin", "Фичефлаги", "Включение и выключение функций"),
    PermissionDef(
        "admin:audit",
        "admin",
        "Журнал аудита и интеграций (чтение)",
        "Сквозной журнал действий и журнал интеграционных событий",
    ),
    PermissionDef(
        "admin:integrations",
        "admin",
        "Повтор доставки интеграций",
        "Ручной повтор события из очереди недоставленных",
        requires=("admin:audit",),
    ),
    PermissionDef(
        "files:others",
        "admin",
        "Импорты, выгрузки и служебные файлы других пользователей",
        "Без этого права сессии импорта и выгрузки видит только их автор",
    ),
)

PERMISSIONS_BY_CODE: dict[str, PermissionDef] = {p.code: p for p in PERMISSIONS}
PERMISSION_CODES: frozenset[str] = frozenset(PERMISSIONS_BY_CODE)
SCOPED_PERMISSIONS: frozenset[str] = frozenset(p.code for p in PERMISSIONS if p.scoped)
PERMISSION_GROUP_CODES: frozenset[str] = frozenset(code for code, _ in PERMISSION_GROUPS)


def permission_name(code: str) -> str:
    definition = PERMISSIONS_BY_CODE.get(code)
    return definition.name if definition is not None else code


# --- системные роли (наборы по умолчанию) ---------------------------------------------------

_OBSERVER = frozenset(
    {
        "requests:read",
        "workflow:read",
        "workflow:history",
        "universities:read",
        "contracts:read",
        "products:read",
        "programs:read",
        "students:read",  # все выдачи маскированы; раскрытие наблюдателю недоступно
        "activities:read",
        "reports:read",
        "reports:all",
        "export:run",  # экспорт с маскированными ПДн
        "notifications:self",
        "admin:audit",  # аудит и журнал интеграций наблюдатель читает
    }
)

_KAM = frozenset(
    {
        "requests:read",
        "requests:write",
        "requests:transition",
        "requests:assign",  # взять свободную заявку на себя
        "requests:kam",
        "requests:files",
        "comments:write",
        "clients:pii",
        "workflow:read",
        "universities:read",
        "universities:write",
        "contracts:read",
        "contracts:write",
        "products:read",
        "programs:read",
        "programs:write",
        "programs:priority",
        "files:business",
        "students:read",
        "students:write",
        "students:reveal",
        "students:files",
        "activities:read",
        "activities:participants",
        "reports:read",
        "export:run",
        "import:run",
        "notifications:self",
    }
)

_HEAD_KAM = (_KAM - {"requests:kam"}) | frozenset(
    {
        "requests:write_any",
        "requests:assign_any",
        "workflow:history",
        "workflow:change",  # пакеты изменений и impact-preview, публикует admin
        "universities:assign",  # ответственные за вузы — право Руководителя (ТЗ, ФТ11)
        "contacts:delete",
        "contracts:files_delete",
        "crm:write_any",
        "products:write",
        "students:files_delete",
        "activities:write",
        "reports:all",
        "notification_rules:read",
        "admin:users",  # список пользователей — чтение
    }
)

# admin — все права, кроме признака «ведёт портфель как КАМ»: администратор не попадает
# в отчёт нагрузки КАМов и в выбор КАМа (как и до ролей в БД)
_ADMIN = PERMISSION_CODES - {"requests:kam"}

SYSTEM_ROLES: dict[str, SystemRoleDef] = {
    "admin": SystemRoleDef(
        "admin",
        "Администратор",
        "Полный доступ. Права и область не редактируются — это защищает систему "
        "от потери управления",
        _ADMIN,
        locked=True,
    ),
    "head_kam": SystemRoleDef(
        "head_kam",
        "Руководитель КАМов",
        "Все заявки и справочники, распределение заявок, правка схемы процесса через согласование",
        _HEAD_KAM,
    ),
    "kam": SystemRoleDef(
        "kam",
        "КАМ",
        "Ведёт свой портфель заявок и вузов; видит все заявки, правит свои",
        _KAM,
    ),
    "observer": SystemRoleDef(
        "observer",
        "Наблюдатель",
        "Только чтение: заявки, справочники, отчёты по всем КАМам, аудит; ПДн маскированы",
        _OBSERVER,
    ),
}
SYSTEM_ROLE_CODES: tuple[str, ...] = tuple(SYSTEM_ROLES)


def missing_requirements(perms: Iterable[str]) -> dict[str, tuple[str, ...]]:
    """Права набора, у которых не хватает зависимостей: `{право: (недостающие, …)}`.

    Неизвестные коды пропускаются (их отклоняет валидация каталога отдельно).
    """
    selected = set(perms)
    result: dict[str, tuple[str, ...]] = {}
    for code in sorted(selected):
        definition = PERMISSIONS_BY_CODE.get(code)
        if definition is None:
            continue
        missing = tuple(req for req in definition.requires if req not in selected)
        if missing:
            result[code] = missing
    return result


def with_requirements(perms: Iterable[str]) -> frozenset[str]:
    """Замыкание набора по `requires` (транзитивно)."""
    result = set(perms)
    stack = list(result)
    while stack:
        definition = PERMISSIONS_BY_CODE.get(stack.pop())
        if definition is None:
            continue
        for req in definition.requires:
            if req not in result:
                result.add(req)
                stack.append(req)
    return frozenset(result)
