#!/usr/bin/env python3
"""Модель архитектуры «Альмы» для Archi: функциональная и компонентная схемы.

Один источник истины — данные этого файла и каталог прав `backend/app/core/rbac_catalog.py`
(по нему рассчитано, какая роль выполняет какую бизнес-функцию). Из них собираются:

- `elements.csv`, `relations.csv`, `properties.csv` — формат Archi «Import → CSV Data Into
  Selected Model» (заголовки и типы — как в CSVConstants Archi, все поля в кавычках, UTF-8);
- `alma.archimate` — та же модель в родном формате Archi с двумя готовыми видами
  («Функциональная схема», «Компонентная схема»): CSV-импорт Archi виды не создаёт;
- блоки Mermaid-превью и сводка в `README.md` между маркерами `<!-- archi:… -->`.

Каждая связь проверяется по таблице допустимых связей ArchiMate из Archi
(`relationships.xml`): CSV-импорт Archi отвергает файл целиком, если хоть одна связь
недопустима. Скрипт работает только на стандартной библиотеке (Python 3.10+).

    python3 docs/architecture/archi/build_archi.py           # перегенерировать файлы
    python3 docs/architecture/archi/build_archi.py --check   # exit 1, если файлы устарели
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import io
import re
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
RBAC_CATALOG = REPO_ROOT / "backend" / "app" / "core" / "rbac_catalog.py"
GENERATOR = "docs/architecture/archi/build_archi.py"

MODEL_ID = "alma-model"
MODEL_NAME = "«Альма» — CRM взаимодействия с вузами"
MODEL_PURPOSE = (
    "Функциональная и компонентная архитектура «Альмы» (ЛЦТ-2026) в нотации ArchiMate 3.1. "
    f"Модель сгенерирована скриптом {GENERATOR} из данных репозитория; "
    "назначения ролей на бизнес-функции рассчитаны по каталогу прав "
    "backend/app/core/rbac_catalog.py."
)
# версия формата файла .archimate: открывается в Archi 4.9 и новее без предупреждений
ARCHI_MODEL_VERSION = "4.9.0"

VIEW_FUNCTIONAL = "Функциональная схема"
VIEW_COMPONENT = "Компонентная схема"
PROP_VIEW = "Вид"

# --- допустимые связи ArchiMate --------------------------------------------------------------

# буквы relationships.xml Archi → тип связи в CSV и .archimate
RELATION_LETTERS: dict[str, str] = {
    "AccessRelationship": "a",
    "CompositionRelationship": "c",
    "FlowRelationship": "f",
    "AggregationRelationship": "g",
    "AssignmentRelationship": "i",
    "InfluenceRelationship": "n",
    "AssociationRelationship": "o",
    "RealizationRelationship": "r",
    "SpecializationRelationship": "s",
    "TriggeringRelationship": "t",
    "ServingRelationship": "v",
}

# выписка из com.archimatetool.model/model/relationships.xml для пар типов этой модели
ALLOWED_RELATIONS: dict[tuple[str, str], str] = {
    ("BusinessActor", "BusinessFunction"): "fiotv",
    ("BusinessRole", "BusinessFunction"): "fiotv",
    ("BusinessRole", "BusinessRole"): "cfgostv",
    ("ApplicationService", "BusinessFunction"): "fotv",
    ("ApplicationComponent", "ApplicationService"): "fiortv",
    ("ApplicationComponent", "ApplicationComponent"): "cfgorstv",
    ("ApplicationComponent", "BusinessActor"): "fotv",
    ("SystemSoftware", "ApplicationComponent"): "fortv",
    ("SystemSoftware", "SystemSoftware"): "cfgiorstv",
    ("Node", "SystemSoftware"): "cfgiortv",
    ("Node", "Artifact"): "aio",
    ("Node", "TechnologyProcess"): "fiortv",
    ("Node", "Grouping"): "acfginorstv",
    ("Artifact", "ApplicationComponent"): "or",
    ("TechnologyProcess", "ApplicationComponent"): "fotv",
    ("TechnologyProcess", "SystemSoftware"): "fotv",
    ("Constraint", "ApplicationComponent"): "o",
    ("Constraint", "Node"): "o",
    ("Constraint", "SystemSoftware"): "o",
    ("Grouping", "BusinessActor"): "cfgiorstv",
    ("Grouping", "BusinessRole"): "cfgiorstv",
    ("Grouping", "BusinessFunction"): "cfgiorstv",
    ("Grouping", "ApplicationService"): "cfgiorstv",
    ("Grouping", "ApplicationComponent"): "cfgorstv",
    ("Grouping", "SystemSoftware"): "cfgiorstv",
    ("Grouping", "Node"): "cfgiorstv",
    ("Grouping", "TechnologyProcess"): "cfgiorstv",
    ("Grouping", "Artifact"): "acgiors",
    ("Grouping", "Constraint"): "cgnors",
}

# папка .archimate по типу элемента
FOLDER_OF_TYPE: dict[str, str] = {
    "BusinessActor": "business",
    "BusinessRole": "business",
    "BusinessFunction": "business",
    "ApplicationComponent": "application",
    "ApplicationService": "application",
    "Node": "technology",
    "SystemSoftware": "technology",
    "Artifact": "technology",
    "TechnologyProcess": "technology",
    "Constraint": "motivation",
    "Grouping": "other",
}
FOLDERS: tuple[tuple[str, str], ...] = (
    ("strategy", "Стратегия"),
    ("business", "Бизнес"),
    ("application", "Приложения"),
    ("technology", "Технологии"),
    ("motivation", "Мотивация"),
    ("implementation_migration", "Реализация и миграция"),
    ("other", "Прочее"),
    ("relations", "Связи"),
    ("diagrams", "Виды"),
)

# порядок — порядок перечисления в сводке README
TYPE_LABELS: dict[str, str] = {
    "BusinessActor": "бизнес-акторы",
    "BusinessRole": "бизнес-роли",
    "BusinessFunction": "бизнес-функции",
    "ApplicationService": "прикладные сервисы",
    "ApplicationComponent": "компоненты приложений",
    "Node": "узлы",
    "SystemSoftware": "системное ПО",
    "TechnologyProcess": "технологические процессы",
    "Artifact": "артефакты",
    "Constraint": "ограничения",
    "Grouping": "группировки",
}
RELATION_LABELS: dict[str, str] = {
    "AssignmentRelationship": "назначение",
    "ServingRelationship": "обслуживание",
    "RealizationRelationship": "реализация",
    "CompositionRelationship": "композиция",
    "AggregationRelationship": "агрегация",
    "FlowRelationship": "поток",
    "TriggeringRelationship": "запуск",
    "AssociationRelationship": "ассоциация",
    "SpecializationRelationship": "специализация",
}


# --- структура модели ------------------------------------------------------------------------


@dataclass
class Element:
    id: str
    type: str
    name: str
    doc: str = ""
    props: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Relation:
    id: str
    type: str
    source: str
    target: str
    name: str = ""


@dataclass
class Box:
    """Объект вида: элемент модели с координатами (вложенные — относительно родителя)."""

    element: str
    x: int
    y: int
    w: int
    h: int
    children: list[Box] = field(default_factory=list)


@dataclass
class View:
    key: str
    name: str
    note: str
    note_box: tuple[int, int, int, int]
    boxes: list[Box]


@dataclass
class Model:
    elements: dict[str, Element] = field(default_factory=dict)
    relations: dict[str, Relation] = field(default_factory=dict)
    views: list[View] = field(default_factory=list)

    def add(self, el_id: str, el_type: str, name: str, doc: str = "", **props: str) -> str:
        if el_id in self.elements:
            raise ValueError(f"элемент {el_id} объявлен дважды")
        prop_list = [(key.replace("_", " "), value) for key, value in props.items() if value]
        self.elements[el_id] = Element(el_id, el_type, name, doc, prop_list)
        return el_id

    def relate(self, rel_type: str, source: str, target: str, name: str = "") -> str:
        letter = RELATION_LETTERS[rel_type]
        rel_id = f"alma-rel-{letter}-{_short(source)}--{_short(target)}"
        if rel_id in self.relations:
            raise ValueError(f"связь {rel_id} объявлена дважды")
        self.relations[rel_id] = Relation(rel_id, rel_type, source, target, name)
        return rel_id


def _short(el_id: str) -> str:
    return el_id.removeprefix("alma-")


def _prop(element: Element, key: str) -> str:
    return next((value for k, value in element.props if k == key), "")


# --- каталог прав ----------------------------------------------------------------------------


def load_rbac_catalog() -> ModuleType:
    """Каталог прав backend: модуль без зависимостей от FastAPI и БД — грузим по пути."""
    spec = importlib.util.spec_from_file_location("alma_rbac_catalog", RBAC_CATALOG)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"не найден каталог прав {RBAC_CATALOG}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses ищет модуль в sys.modules
    spec.loader.exec_module(module)
    return module


# --- функциональная схема: данные ------------------------------------------------------------

# порядок ролей на схеме; имена и наборы прав — из каталога
ROLE_ORDER = ("kam", "head_kam", "admin", "observer")

# колонки схемы: группа прикладных сервисов и бизнес-функции над ней
SERVICE_GROUPS: tuple[tuple[str, str], ...] = (
    ("registries", "Реестры"),
    ("processes", "Процессы и заявки"),
    ("talent", "Пул талантов"),
    ("reports", "Отчёты и аналитика"),
    ("access", "Роли и доступ"),
    ("integrations", "Интеграции и загрузка данных"),
    ("notifications", "Уведомления"),
    ("common", "Общие сервисы"),
)

ALL_ROLES = "*"


@dataclass(frozen=True)
class FunctionDef:
    key: str
    name: str
    column: str
    # права, обладание любым из которых означает, что роль выполняет функцию; ALL_ROLES — все
    acting: tuple[str, ...]
    doc: str


FUNCTIONS: tuple[FunctionDef, ...] = (
    FunctionDef(
        "universities",
        "Ведение вузов и контактов",
        "registries",
        ("universities:write",),
        "Карточки вузов, контакты ответственных лиц, регион, закреплённый КАМ.",
    ),
    FunctionDef(
        "contracts",
        "Ведение договоров, продуктов и программ",
        "registries",
        ("contracts:write", "products:write", "programs:write", "programs:priority"),
        "Договоры по схеме «1 договор — N продуктов» с лицензиями и файлами, каталог "
        "продуктов, программы обучения и их ручной приоритет.",
    ),
    FunctionDef(
        "requests",
        "Обработка заявок B2B и B2C",
        "processes",
        ("requests:write", "requests:transition"),
        "Заявки вузов (B2B) и физических и юридических лиц (B2C): создание, переходы по "
        "этапам процесса, комментарии с @упоминаниями, файлы, назначение ответственного.",
    ),
    FunctionDef(
        "workflow",
        "Управление процессами и согласование",
        "processes",
        ("workflow:change", "workflow:approve"),
        "Правка графа этапов в визуальном конструкторе. Опасные правки уходят на "
        "согласование администратору; после публикации открытые заявки переводятся на "
        "новую схему одной транзакцией.",
    ),
    FunctionDef(
        "talent",
        "Работа с пулом талантов",
        "talent",
        ("students:write", "activities:write", "activities:participants"),
        "Студенты и воронка «кандидат → пул талантов», активности и расписание, запись "
        "студентов на активности. ПДн маскируются по умолчанию.",
    ),
    FunctionDef(
        "kam-control",
        "Контроль активности КАМов и сроков",
        "reports",
        ("reports:all",),
        "Кто, когда и с какими вузами взаимодействовал, на каких этапах заявки "
        "останавливаются, какие этапы просрочены — по всем КАМам области роли.",
    ),
    FunctionDef(
        "analytics",
        "Аналитика и отчётность",
        "reports",
        ("reports:read", "export:run"),
        "Интерактивные панели с фильтрами и детализацией, выгрузка реестров и отчётов.",
    ),
    FunctionDef(
        "access",
        "Управление доступом: роли и права",
        "access",
        ("admin:roles", "admin:users_write"),
        "Свои роли из каталога прав без программиста, область роли (процессы B2B и B2C, "
        "записи «все» или «только свои»), назначение ролей, руководители для эскалаций.",
    ),
    FunctionDef(
        "audit",
        "Аудит и защита персональных данных",
        "access",
        ("admin:audit",),
        "Журнал действий пользователей, раскрытий ПДн и обмена с внешними системами.",
    ),
    FunctionDef(
        "exchange",
        "Обмен данными с LMS и CMS",
        "integrations",
        ("admin:integrations",),
        "Двусторонний обмен с LMS и CMS: единый JSON-конверт с HMAC-подписью, outbox, "
        "повторные попытки и очередь недоставленных; администратор контролирует журнал и "
        "повторяет доставку.",
    ),
    FunctionDef(
        "import",
        "Загрузка данных из файлов",
        "integrations",
        ("import:run", "import:dictionaries"),
        "Диалоговый импорт XLSX, XLS, CSV и JSON: сопоставление колонок с шаблонами, "
        "устойчивость к кодировкам, построчный отчёт ошибок.",
    ),
    FunctionDef(
        "notify",
        "Оповещение и эскалация",
        "notifications",
        ("notifications:self", "notification_rules:write"),
        "Уведомления в интерфейсе и в Telegram, правила и пороги, эскалация зависших заявок "
        "по каскаду «ответственный → руководитель».",
    ),
    FunctionDef(
        "settings",
        "Ведение справочников и настроек",
        "common",
        ("admin:dictionaries", "admin:settings", "admin:flags"),
        "Справочники, ИТ-направления, фичефлаги и системные настройки.",
    ),
    FunctionDef(
        "selfservice",
        "Адаптация и самообслуживание",
        "common",
        (ALL_ROLES,),
        "Встроенная справка, интерактивные туры по ролям, глобальный поиск, личные пресеты "
        "реестров — доступны каждому пользователю.",
    ),
)


@dataclass(frozen=True)
class ServiceDef:
    key: str
    name: str
    group: str
    serves: tuple[str, ...]
    api: str
    perms: str
    screen: str
    realized_by: tuple[str, ...]
    doc: str


# realized_by — ключи компонентов компонентной схемы (модули backend и сервисы)
SERVICES: tuple[ServiceDef, ...] = (
    # --- реестры ---
    ServiceDef(
        "universities",
        "Реестр вузов и контактов",
        "registries",
        ("universities",),
        "/api/v1/universities*",
        "universities:read, universities:write, universities:delete, contacts:delete",
        "/registry/universities",
        ("mod-crm",),
        "Реестр и карточка вуза: контакты, регион, ответственный КАМ, связанные договоры.",
    ),
    ServiceDef(
        "contracts",
        "Реестр договоров",
        "registries",
        ("contracts",),
        "/api/v1/contracts*",
        "contracts:read, contracts:write, contracts:delete, contracts:files_delete",
        "/registry/contracts",
        ("mod-crm", "mod-files"),
        "Договор «1 договор — N продуктов», лицензии по продуктам, файлы договора.",
    ),
    ServiceDef(
        "products",
        "Каталог продуктов",
        "registries",
        ("contracts",),
        "/api/v1/products*",
        "products:read, products:write, products:delete",
        "/registry/products",
        ("mod-crm",),
        "Каталог ИТ-продуктов и вендоров.",
    ),
    ServiceDef(
        "programs",
        "Программы и ручной приоритет",
        "registries",
        ("contracts",),
        "/api/v1/programs*",
        "programs:read, programs:write, programs:priority, programs:delete",
        "/registry/programs",
        ("mod-crm",),
        "Программы обучения, их ручное ранжирование, востребованность по заявкам.",
    ),
    # --- процессы и заявки ---
    ServiceDef(
        "board",
        "Реестр и доска заявок",
        "processes",
        ("requests",),
        "/api/v1/requests; /api/v1/requests/board",
        "requests:read, requests:write",
        "/board",
        ("mod-requests",),
        "Канбан по этапам процесса с подсветкой разрешённых переходов и реестр заявок.",
    ),
    ServiceDef(
        "request-card",
        "Карточка заявки: комментарии, файлы",
        "processes",
        ("requests",),
        "/api/v1/requests/{request_id}*",
        "requests:read, requests:write, comments:write, requests:files, requests:reopen",
        "/requests/:id",
        ("mod-requests", "mod-files"),
        "Карточка заявки: история переходов, комментарии с @упоминаниями, файлы по этапам, "
        "переоткрытие закрытых заявок.",
    ),
    ServiceDef(
        "transitions",
        "Переходы по графу этапов",
        "processes",
        ("requests",),
        "/api/v1/requests/{request_id}/transitions",
        "requests:transition",
        "/board",
        ("mod-workflow", "mod-requests"),
        "Переход проверяется по живой схеме процесса: разрешённое ребро, роль, обязательный "
        "комментарий у возврата; история пишется в той же транзакции.",
    ),
    ServiceDef(
        "assign",
        "Назначение и передача портфеля",
        "processes",
        ("requests",),
        "/api/v1/requests/{request_id}/assign; /api/v1/requests/transfer-portfolio",
        "requests:assign, requests:assign_any, universities:assign",
        "/board",
        ("mod-requests",),
        "Взять заявку на себя, назначить ответственного, передать открытые заявки и вузы "
        "уходящего КАМа другому сотруднику одним действием.",
    ),
    ServiceDef(
        "designer",
        "Конструктор процессов",
        "processes",
        ("workflow",),
        "/api/v1/workflows*",
        "workflow:read, workflow:history, workflow:change",
        "/workflow",
        ("mod-workflow",),
        "Визуальный редактор графа этапов: ветвления, возвраты, пороги зависания, "
        "предпросмотр влияния правки на открытые заявки.",
    ),
    ServiceDef(
        "approvals",
        "Согласование изменений схемы",
        "processes",
        ("workflow",),
        "/api/v1/workflows/changes/{change_id}/approve; "
        "/api/v1/workflows/changes/{change_id}/reject",
        "workflow:approve",
        "/admin/approvals",
        ("mod-workflow",),
        "Пакет опасных правок уходит на согласование; публикация — после одобрения.",
    ),
    ServiceDef(
        "migration",
        "Перевод заявок на новую схему",
        "processes",
        ("workflow",),
        "/api/v1/workflows/changes/{change_id}/approve",
        "workflow:approve",
        "/admin/approvals",
        ("mod-workflow",),
        "При публикации схемы открытые заявки удаляемых этапов переводятся одной "
        "транзакцией: один SQL-оператор на этап (modules/workflow/migration.py).",
    ),
    # --- пул талантов ---
    ServiceDef(
        "students",
        "Реестр студентов",
        "talent",
        ("talent",),
        "/api/v1/students*",
        "students:read, students:write, students:files, students:delete",
        "/talent-pool",
        ("mod-talent",),
        "Студенты и смена статуса по воронке «кандидат → пул талантов»; ПДн в БД "
        "зашифрованы, в интерфейсе маскированы.",
    ),
    ServiceDef(
        "reveal",
        "Раскрытие ПДн с аудитом",
        "talent",
        ("talent", "audit"),
        "/api/v1/students/{student_id}/reveal",
        "students:reveal",
        "/talent-pool/students/:id",
        ("mod-talent",),
        "Раскрытие персональных данных студента — только по праву и с записью в аудит.",
    ),
    ServiceDef(
        "activities",
        "Активности и расписание",
        "talent",
        ("talent",),
        "/api/v1/activities*",
        "activities:read, activities:write, activities:participants",
        "/talent-pool",
        ("mod-talent",),
        "Мероприятия и курсы с расписанием, запись студентов.",
    ),
    # --- отчёты ---
    ServiceDef(
        "dashboard",
        "Аналитическая панель",
        "reports",
        ("analytics",),
        "/api/v1/reports/dashboard; /api/v1/reports/funnel; /api/v1/reports/kam-workload; "
        "/api/v1/reports/stuck; /api/v1/reports/universities-coverage; "
        "/api/v1/reports/programs-demand; /api/v1/reports/talent-pool-funnel; "
        "/api/v1/reports/dynamics",
        "reports:read, reports:all",
        "/dashboard",
        ("mod-reporting",),
        "Воронка, нагрузка КАМов, зависшие заявки, покрытие вузов, востребованность "
        "программ, пул талантов, динамика; фильтры и экспорт PNG.",
    ),
    ServiceDef(
        "kam-activity",
        "Отчёт «Активность КАМов»",
        "reports",
        ("kam-control",),
        "/api/v1/reports/kam-activity*",
        "reports:read, reports:all",
        "/reports/kam-activity",
        ("mod-reporting",),
        "Детализация «КАМ → вузы → заявки» и сводка для заказчика: KPI с дельтами, "
        "конверсия по этапам, где застревают заявки; выгрузка XLSX и PDF.",
    ),
    ServiceDef(
        "interactions",
        "Отчёт «Взаимодействия за период»",
        "reports",
        ("kam-control", "analytics"),
        "/api/v1/reports/interactions",
        "reports:read, reports:all",
        "/reports/kam-activity",
        ("mod-reporting",),
        "Взаимодействия КАМов с вузами за период по колонкам отчёта, с выгрузкой.",
    ),
    ServiceDef(
        "sla",
        "Просрочки по срокам этапов",
        "reports",
        ("kam-control",),
        "/api/v1/reports/sla",
        "reports:read, reports:all",
        "/reports/kam-activity",
        ("mod-reporting",),
        "Вкладка «Просрочки» отчёта «Активность КАМов»: заявки дольше нормы этапа, "
        "корзины задержки, атрибуция на дату.",
    ),
    ServiceDef(
        "ai-summary",
        "Сводка ИИ по отчёту КАМов",
        "reports",
        ("kam-control",),
        "/api/v1/reports/ai-summaries*",
        "reports:ai_summary",
        "/reports/kam-activity",
        ("mod-ai-summary", "ac-llm-gateway"),
        "Сводка фактов по команде и по КАМу поверх цифр «Сводки для заказчика» и "
        "«Просрочек»: GigaChat через llm-gateway получает только агрегаты и псевдонимы, "
        "числа ответа сверяются с отчётом; без GigaChat — сводка по правилам. За фичефлагом "
        "ai_summaries.",
    ),
    ServiceDef(
        "export",
        "Экспорт XLSX, CSV, JSON, PDF",
        "reports",
        ("analytics",),
        "/api/v1/export/jobs*",
        "export:run",
        "/dashboard",
        ("mod-import-export",),
        "Выгрузка реестров и отчётов; ПДн маскируются по правам выгружающего.",
    ),
    # --- роли и доступ ---
    ServiceDef(
        "login",
        "Вход и профиль доступа",
        "access",
        ("access",),
        "/api/v1/auth/me",
        "",
        "/login",
        ("mod-auth", "ac-keycloak"),
        "Вход через self-hosted Keycloak (OIDC + PKCE); профиль отдаёт роли, права и "
        "область по процессам. Каждая проверка права — на backend.",
    ),
    ServiceDef(
        "roles",
        "Роли и права",
        "access",
        ("access",),
        "/api/v1/admin/roles*; /api/v1/admin/permissions; /api/v1/roles",
        "admin:roles",
        "/admin/roles",
        ("mod-access",),
        "Редактор ролей: каталог прав по группам, зависимости прав, область роли; "
        "изменения действуют без перезахода.",
    ),
    ServiceDef(
        "users",
        "Пользователи и назначение ролей",
        "access",
        ("access",),
        "/api/v1/admin/users*; /api/v1/admin/users/{user_id}/roles",
        "admin:users, admin:users_write, admin:roles",
        "/admin/users",
        ("mod-admin", "mod-access"),
        "Список пользователей, назначение ролей, руководители для эскалаций, "
        "синхронизация с Keycloak.",
    ),
    ServiceDef(
        "audit",
        "Журнал аудита",
        "access",
        ("audit",),
        "/api/v1/admin/audit-log",
        "admin:audit",
        "/admin/audit",
        ("mod-admin",),
        "Журнал действий (append-only), в том числе раскрытий ПДн.",
    ),
    # --- интеграции и загрузка данных ---
    ServiceDef(
        "lms-in",
        "Приём событий LMS",
        "integrations",
        ("exchange",),
        "/api/v1/integrations/lms/webhook",
        "",
        "",
        ("mod-integrations",),
        "Вебхук LMS: JSON-конверт с HMAC-подписью, идемпотентность по event_id.",
    ),
    ServiceDef(
        "cms-in",
        "Приём лидов CMS",
        "integrations",
        ("exchange",),
        "/api/v1/integrations/cms/webhook",
        "",
        "",
        ("mod-integrations",),
        "Вебхук CMS: лиды с сайта становятся заявками B2C.",
    ),
    ServiceDef(
        "outbox",
        "Отправка событий (outbox, DLQ)",
        "integrations",
        ("exchange",),
        "/api/v1/internal/jobs/outbox-relay",
        "",
        "",
        ("mod-integrations", "mod-internal"),
        "Транзакционный outbox в PostgreSQL, ретраи 1 м → 5 м → 30 м → 2 ч → 6 ч, затем "
        "очередь недоставленных; транспорт — сменный адаптер.",
    ),
    ServiceDef(
        "intg-journal",
        "Журнал обмена и повтор",
        "integrations",
        ("exchange",),
        "/api/v1/admin/integration/events*",
        "admin:audit, admin:integrations",
        "/admin/integrations",
        ("mod-integrations",),
        "Журнал событий обмена с LMS и CMS и ручной повтор доставки.",
    ),
    ServiceDef(
        "import",
        "Мастер импорта файлов",
        "integrations",
        ("import",),
        "/api/v1/import/sessions*",
        "import:run, import:dictionaries",
        "/import",
        ("mod-import-export",),
        "XLSX, XLS (BIFF), CSV и JSON: автоопределение кодировки, сопоставление колонок, "
        "проверка и применение с построчным отчётом ошибок.",
    ),
    # --- уведомления ---
    ServiceDef(
        "inbox",
        "Уведомления в интерфейсе",
        "notifications",
        ("notify",),
        "/api/v1/notifications; /api/v1/notifications/read",
        "notifications:self",
        "/settings/notifications",
        ("mod-notifications",),
        "Лента уведомлений пользователя и отметка о прочтении.",
    ),
    ServiceDef(
        "notify-settings",
        "Каналы и привязка Telegram",
        "notifications",
        ("notify",),
        "/api/v1/notifications/settings; /api/v1/notifications/channels; "
        "/api/v1/notifications/telegram/link-code",
        "notifications:self",
        "/settings/notifications",
        ("mod-notifications",),
        "Личные настройки каналов, привязка Telegram по коду, тестовое сообщение.",
    ),
    ServiceDef(
        "rules",
        "Правила уведомлений",
        "notifications",
        ("notify",),
        "/api/v1/notifications/rules*",
        "notification_rules:read, notification_rules:write",
        "/settings/notifications",
        ("mod-notifications",),
        "Правила рассылки по событиям и пороги эскалации.",
    ),
    ServiceDef(
        "bot",
        "Telegram-бот",
        "notifications",
        ("notify",),
        "/api/v1/internal/bot/bind; /api/v1/internal/bot/users/{chat_id}/requests",
        "",
        "",
        ("ac-notification", "mod-notifications"),
        "Уведомления и команды бота строго по правам привязанного пользователя; "
        "вход сервиса — client credentials Keycloak.",
    ),
    ServiceDef(
        "stuck",
        "Эскалация зависших заявок",
        "notifications",
        ("notify", "kam-control"),
        "/api/v1/internal/jobs/check-stuck-requests",
        "",
        "",
        ("mod-internal", "mod-workflow", "mod-notifications"),
        "Ежечасная проверка порогов зависания (этап → процесс → глобальная настройка): "
        "уведомление ответственному при 1× порога, руководителю при 1,5×.",
    ),
    # --- общие сервисы ---
    ServiceDef(
        "dictionaries",
        "Справочники",
        "common",
        ("settings",),
        "/api/v1/admin/dictionaries*",
        "admin:dictionaries, import:dictionaries",
        "/admin/dictionaries",
        ("mod-import-export",),
        "Справочники системы с правкой в интерфейсе и импортом из файла.",
    ),
    ServiceDef(
        "directions",
        "ИТ-направления",
        "common",
        ("settings",),
        "/api/v1/it-directions*",
        "admin:dictionaries",
        "/admin/dictionaries",
        ("mod-crm",),
        "Справочник ИТ-направлений для вузов, продуктов и отчётов.",
    ),
    ServiceDef(
        "flags",
        "Фичефлаги и настройки",
        "common",
        ("settings",),
        "/api/v1/admin/feature-flags*; /api/v1/admin/settings; /api/v1/flags",
        "admin:flags, admin:settings",
        "/admin/feature-flags",
        ("mod-admin",),
        "Включение функций без выкладки и системные пороги.",
    ),
    ServiceDef(
        "files",
        "Файлы и вложения",
        "common",
        ("requests", "contracts"),
        "/api/v1/files/{file_id}/download",
        "files:business, files:others",
        "",
        ("mod-files",),
        "Файлы заявок, договоров и студентов в MinIO; скачивание по правам.",
    ),
    ServiceDef(
        "sse",
        "Обновление экранов (SSE)",
        "common",
        ("requests",),
        "/api/v1/events/stream",
        "",
        "",
        ("mod-ui", "mod-core"),
        "Server-Sent Events: публикация схемы, переходы, комментарии и смена прав "
        "обновляют открытые экраны без перезагрузки.",
    ),
    ServiceDef(
        "search",
        "Глобальный поиск",
        "common",
        ("selfservice",),
        "/api/v1/search",
        "",
        "",
        ("mod-ui",),
        "Поиск по вузам, заявкам, договорам и другим сущностям в области прав.",
    ),
    ServiceDef(
        "presets",
        "Пресеты реестров",
        "common",
        ("selfservice",),
        "/api/v1/ui/presets*",
        "",
        "",
        ("mod-ui",),
        "Личные наборы колонок и фильтров реестров.",
    ),
    ServiceDef(
        "help",
        "Встроенная справка и туры",
        "common",
        ("selfservice",),
        "",
        "",
        "/help",
        ("ac-frontend",),
        "Руководство пользователя и администратора внутри интерфейса, туры по ролям, "
        "контрольный список «Начало работы».",
    ),
)

EXTERNAL_ACTORS: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    (
        "university",
        "Вуз-партнёр",
        ("universities", "requests"),
        "Сторона взаимодействия: контакты вуза, заявки B2B, договоры и обучение.",
    ),
    (
        "client",
        "Клиент или абитуриент",
        ("requests", "exchange"),
        "Физическое или юридическое лицо: заявки B2C, в том числе лиды с сайта через CMS.",
    ),
)


# --- компонентная схема: данные --------------------------------------------------------------

# модули backend/app/modules (core — сквозное ядро backend/app/core)
MODULES: tuple[tuple[str, str, str, str], ...] = (
    ("auth", "auth — вход и профиль", "app/modules/auth", "Профиль, роли и права из БД."),
    (
        "access",
        "access — роли и права",
        "app/modules/access",
        "Роли, каталог прав, назначение ролей; загрузка каталога из кода.",
    ),
    (
        "workflow",
        "workflow — движок процессов",
        "app/modules/workflow",
        "Графы этапов, переходы, пакеты изменений, перевод заявок, пороги зависания.",
    ),
    (
        "requests",
        "requests — заявки",
        "app/modules/requests",
        "Заявки B2B и B2C, доска, комментарии, файлы, передача портфеля.",
    ),
    (
        "crm",
        "crm — вузы, договоры, каталог",
        "app/modules/crm",
        "Вузы и контакты, договоры, продукты, программы, ИТ-направления.",
    ),
    (
        "import-export",
        "import_export — импорт и экспорт",
        "app/modules/import_export",
        "Мастер импорта, выгрузки XLSX/CSV/JSON/PDF, справочники.",
    ),
    (
        "talent",
        "talent_pool — пул талантов",
        "app/modules/talent_pool",
        "Студенты с шифрованием ПДн, активности и расписание.",
    ),
    (
        "reporting",
        "reporting — отчёты",
        "app/modules/reporting",
        "Агрегаты дашборда, отчёт «Активность КАМов», PDF (fpdf2) и XLSX.",
    ),
    (
        "ai-summary",
        "ai_summary — сводки ИИ",
        "app/modules/ai_summary",
        "Факты отчётов с псевдонимами КАМов, задачи и кэш (ai_summary_job), вызов "
        "llm-gateway, проверка чисел и советов, сводка по правилам.",
    ),
    (
        "integrations",
        "integrations — LMS и CMS",
        "app/modules/integrations",
        "Вебхуки LMS и CMS, конверт с HMAC, outbox, журнал обмена.",
    ),
    (
        "notifications",
        "notifications — уведомления",
        "app/modules/notifications",
        "Лента, правила, каналы, API бота, relay в notification-service.",
    ),
    ("files", "files — файлы", "app/modules/files", "Хранение и выдача файлов в MinIO."),
    (
        "admin",
        "admin — администрирование",
        "app/modules/admin",
        "Пользователи, настройки, фичефлаги, журнал аудита.",
    ),
    ("ui", "ui — SSE, поиск, пресеты", "app/modules/ui", "Поток событий SSE, поиск, пресеты."),
    (
        "internal",
        "internal — служебные задания",
        "app/modules/internal",
        "Эндпоинты CronJob: зависшие заявки, очистки, relay outbox (X-Internal-Token).",
    ),
    (
        "core",
        "core — сквозные механизмы",
        "app/core",
        "Ядро прав (require_permission), Idempotency-Key, outbox, SSE-хаб, кэш KeyDB, "
        "шифрование ПДн, аудит.",
    ),
)


def _module_id(key: str) -> str:
    return f"alma-mod-{key}"


# --- сборка модели ---------------------------------------------------------------------------


def build_model(rbac: ModuleType) -> Model:
    model = Model()
    _build_functional(model, rbac)
    _build_component(model)
    # прикладные сервисы реализуются компонентами: трассировка между двумя видами
    for svc in SERVICES:
        for comp in svc.realized_by:
            model.relate("RealizationRelationship", f"alma-{comp}", f"alma-svc-{svc.key}")
    return model


def _build_functional(model: Model, rbac: ModuleType) -> None:
    roles = rbac.SYSTEM_ROLES
    vf = {PROP_VIEW: VIEW_FUNCTIONAL}

    grp_roles = model.add(
        "alma-grp-roles",
        "Grouping",
        "Роли пользователей",
        "Системные роли засевает миграция; свои роли администратор собирает из каталога "
        "прав на экране «Роли и права» и ограничивает процессами (B2B, B2C) и областью "
        "записей («все» или «только свои»).",
        **vf,
    )
    for code in ROLE_ORDER:
        role = roles[code]
        rid = model.add(
            f"alma-role-{code.replace('_', '-')}",
            "BusinessRole",
            role.name,
            role.description,
            **vf,
            Код_роли=code,
            Прав_в_наборе=str(len(role.permissions)),
            Источник="backend/app/core/rbac_catalog.py",
        )
        model.relate("AggregationRelationship", grp_roles, rid)
    custom = model.add(
        "alma-role-custom",
        "BusinessRole",
        "Свои роли: «КАМ B2B», «КАМ B2C»",
        "Пример ролей, собранных администратором без программиста: права КАМа в одном "
        "процессе (демо-сид). Любая своя роль — набор прав из каталога и область.",
        **vf,
        Код_роли="kam_b2b, kam_b2c",
        Источник="backend/app/tools/seed.py",
    )
    model.relate("AggregationRelationship", grp_roles, custom)
    model.relate("SpecializationRelationship", custom, "alma-role-kam")

    grp_actors = model.add(
        "alma-grp-actors",
        "Grouping",
        "Внешние участники",
        "Стороны взаимодействия, с которыми работают пользователи CRM.",
        **vf,
    )
    for key, name, functions, doc in EXTERNAL_ACTORS:
        aid = model.add(f"alma-actor-{key}", "BusinessActor", name, doc, **vf)
        model.relate("AggregationRelationship", grp_actors, aid)
        for fn in functions:
            model.relate("AssociationRelationship", aid, f"alma-fn-{fn}")

    grp_fn = model.add(
        "alma-grp-functions",
        "Grouping",
        "Бизнес-функции",
        "Что делают пользователи «Альмы». Назначение роли на функцию рассчитано по "
        "каталогу прав: роль выполняет функцию, если в её наборе есть хотя бы одно из прав "
        "функции (свойство «Права функции»).",
        **vf,
    )
    columns = {key for key, _ in SERVICE_GROUPS}
    for fn in FUNCTIONS:
        if fn.column not in columns:
            raise ValueError(f"функция {fn.key}: неизвестная колонка {fn.column}")
        acting = "все роли" if fn.acting == (ALL_ROLES,) else ", ".join(fn.acting)
        fid = model.add(
            f"alma-fn-{fn.key}",
            "BusinessFunction",
            fn.name,
            fn.doc,
            **vf,
            Права_функции=acting,
        )
        model.relate("AggregationRelationship", grp_fn, fid)

    # назначения ролей — по каталогу прав
    for code in ROLE_ORDER:
        perms = roles[code].permissions
        for fn in FUNCTIONS:
            if fn.acting == (ALL_ROLES,) or perms & set(fn.acting):
                model.relate(
                    "AssignmentRelationship",
                    f"alma-role-{code.replace('_', '-')}",
                    f"alma-fn-{fn.key}",
                )

    for key, name in SERVICE_GROUPS:
        model.add(
            f"alma-grp-svc-{key}",
            "Grouping",
            name,
            "Группа прикладных сервисов «Альмы».",
            **vf,
        )
    for svc in SERVICES:
        sid = model.add(
            f"alma-svc-{svc.key}",
            "ApplicationService",
            svc.name,
            svc.doc,
            **vf,
            API=svc.api,
            Права=svc.perms,
            Экран=svc.screen,
        )
        model.relate("AggregationRelationship", f"alma-grp-svc-{svc.group}", sid)
        for fn in svc.serves:
            model.relate("ServingRelationship", sid, f"alma-fn-{fn}")


def _build_component(model: Model) -> None:
    vc = {PROP_VIEW: VIEW_COMPONENT}
    add = model.add
    rel = model.relate

    users = add(
        "alma-actor-users",
        "BusinessActor",
        "Пользователи CRM (браузер, VPN)",
        "КАМы, руководители, администраторы, наблюдатели и свои роли; работают из браузера "
        "внутри закрытого контура.",
        **vc,
    )

    # внешние системы
    grp_ext = add(
        "alma-grp-external",
        "Grouping",
        "Внешние системы (вне закрытого контура)",
        "Системы заказчика и внешние каналы уведомлений.",
        **vc,
    )
    ext = {
        "lms": (
            "LMS заказчика",
            "Система обучения: двусторонний обмен заявками, статусами и событиями обучения. "
            "На стенде её заменяет lms-stub.",
        ),
        "cms": (
            "CMS-сайт заказчика",
            "Сайт: каталог программ и лиды. На стенде её заменяет cms-stub.",
        ),
        "telegram": (
            "Telegram Bot API",
            "Доставка уведомлений и команды бота; доступен только из notification-service.",
        ),
        "channels": (
            "Email (Exchange) и Max",
            "Каналы-заглушки notification-service: сообщение пишется в журнал, адаптер "
            "заменяется без смены кода ядра.",
        ),
        "gigachat": (
            "GigaChat API (Сбер)",
            "Модель для «Сводки ИИ»: OAuth ngw.devices.sberbank.ru:9443, чат "
            "gigachat.devices.sberbank.ru:443; доступен только из llm-gateway. Оператор в РФ; "
            "on-prem GigaChat подключается переменными GIGACHAT_*_URL.",
        ),
    }
    for key, (name, doc) in ext.items():
        eid = add(f"alma-ext-{key}", "ApplicationComponent", name, doc, **vc)
        rel("AggregationRelationship", grp_ext, eid)

    # кластер и зоны
    node = add(
        "alma-node-k8s",
        "Node",
        "Kubernetes: namespace crm",
        "Кластер k3s на ВМ, k3d или minikube; манифесты — kustomize base и overlays. "
        "Default-deny NetworkPolicy, разрешения точка-точка между подами.",
        **vc,
        Манифесты="deploy/k8s",
        Пространство_имён="crm",
    )
    tiers = {
        "app": ("tier=app — приложения", "Интерфейс, ядро CRM и заглушки LMS/CMS."),
        "iam": ("tier=iam — вход", "Self-hosted Keycloak, realm crm."),
        "dmz": (
            "tier=dmz — выход наружу",
            "notification-service и llm-gateway; в целевой архитектуре — отдельный сегмент DMZ.",
        ),
        "data": ("tier=data — хранилища", "PostgreSQL, MinIO, KeyDB."),
    }
    for key, (name, doc) in tiers.items():
        gid = add(f"alma-grp-tier-{key}", "Grouping", name, doc, **vc, Метка=f"tier={key}")
        rel("AggregationRelationship", node, gid)

    ingress = add(
        "alma-sw-ingress",
        "SystemSoftware",
        "Ingress-контроллер",
        "Traefik в k3s и k3d (на ВМ — с TLS от cert-manager), ingress-nginx в minikube: "
        "/ → интерфейс, /api → ядро, отдельные хосты id, lms, cms и консоль MinIO.",
        **vc,
        Манифест="deploy/k8s/base/ingress",
    )
    rel("CompositionRelationship", node, ingress)
    rel("AggregationRelationship", "alma-grp-tier-app", ingress)

    components = {
        "frontend": (
            "frontend — интерфейс",
            "SPA на React 18 и TypeScript: реестры, доска, конструктор процессов, панели "
            "ECharts, справка. Отдаётся nginx-unprivileged.",
            "app",
            {
                "Каталог": "frontend",
                "Технологии": "React 18, TypeScript, Vite, Tailwind CSS v4, TanStack Query и "
                "Table, @xyflow/react, ECharts, keycloak-js",
                "Порт": "8080 (Service 80)",
                "Манифест": "deploy/k8s/base/frontend",
            },
        ),
        "backend": (
            "backend — ядро CRM",
            "Модульный монолит FastAPI: REST /api/v1 со Swagger, SSE, outbox, задания "
            "CronJob. Один образ для API, миграций и засева.",
            "app",
            {
                "Каталог": "backend",
                "Технологии": "Python 3.12, FastAPI, SQLAlchemy 2 (async), Alembic, "
                "Pydantic v2, Uvicorn",
                "Порт": "8000",
                "Манифест": "deploy/k8s/base/backend",
            },
        ),
        "lms-stub": (
            "lms-stub — заглушка LMS",
            "Отдельный под с журналом обмена: принимает события CRM и шлёт вебхуки.",
            "app",
            {
                "Каталог": "services/lms-stub",
                "Технологии": "FastAPI, SQLite",
                "Порт": "8020",
                "Манифест": "deploy/k8s/base/lms-stub",
            },
        ),
        "cms-stub": (
            "cms-stub — заглушка CMS",
            "Отдельный под: демо-форма лида и журнал обмена.",
            "app",
            {
                "Каталог": "services/cms-stub",
                "Технологии": "FastAPI, SQLite",
                "Порт": "8030",
                "Манифест": "deploy/k8s/base/cms-stub",
            },
        ),
        "keycloak": (
            "Keycloak 26 — вход",
            "Self-hosted Keycloak, realm crm: OIDC + PKCE для интерфейса, client "
            "credentials для сервисов. Роли и права — в БД CRM, Keycloak отвечает за вход.",
            "iam",
            {
                "Каталог": "deploy/keycloak",
                "Технологии": "Keycloak 26 (start --import-realm)",
                "Порт": "8080",
                "Манифест": "deploy/k8s/base/keycloak",
            },
        ),
        "notification": (
            "notification-service",
            "Служба уведомлений и Telegram-бот: HTTP-relay каналов и long-polling aiogram. "
            "Выход в интернет — только к Telegram; токен бота видит только он.",
            "dmz",
            {
                "Каталог": "services/notification",
                "Технологии": "FastAPI, aiogram 3",
                "Порт": "8010",
                "Манифест": "deploy/k8s/base/notification",
            },
        ),
        "llm-gateway": (
            "llm-gateway — шлюз GigaChat",
            "Единственный, кто знает ключ GigaChat и ходит к нему (443, 9443): очередь на "
            "один поток, дневной лимит токенов, OAuth и TLS НУЦ Минцифры. Промпт, факты и "
            "проверка ответа — в модуле ai_summary backend.",
            "dmz",
            {
                "Каталог": "services/llm-gateway",
                "Технологии": "FastAPI, httpx",
                "Порт": "8040",
                "Манифест": "deploy/k8s/base/llm-gateway",
            },
        ),
    }
    for key, (name, doc, tier, props) in components.items():
        cid = f"alma-ac-{key}"
        model.elements[cid] = Element(cid, "ApplicationComponent", name, doc, list(vc.items()))
        model.elements[cid].props.extend(props.items())
        rel("AggregationRelationship", f"alma-grp-tier-{tier}", cid)

    for key, name, path, doc in MODULES:
        mid = add(
            _module_id(key),
            "ApplicationComponent",
            name,
            doc,
            **vc,
            Каталог=f"backend/{path}",
        )
        rel("CompositionRelationship", "alma-ac-backend", mid)

    stores = {
        "postgres": (
            "PostgreSQL 16",
            "БД crm и keycloak: данные CRM, роли и права, outbox, ключи идемпотентности; "
            "ПДн студентов — шифртекст.",
            {"Порт": "5432", "Манифест": "deploy/k8s/base/postgres"},
        ),
        "keydb": (
            "KeyDB — кэш и pub/sub",
            "Cache-aside crm:v1:*, рассылка событий SSE между репликами, идемпотентность "
            "отправки уведомлений; без персистентности.",
            {"Порт": "6379", "Манифест": "deploy/k8s/base/keydb"},
        ),
        "minio": (
            "MinIO — файлы (S3)",
            "Бакеты файлов, импортов и выгрузок.",
            {"Порт": "9000, 9001", "Манифест": "deploy/k8s/base/minio"},
        ),
    }
    for key, (name, doc, props) in stores.items():
        sid = f"alma-sw-{key}"
        model.elements[sid] = Element(sid, "SystemSoftware", name, doc, list(vc.items()))
        model.elements[sid].props.extend(props.items())
        rel("CompositionRelationship", node, sid)
        rel("AggregationRelationship", "alma-grp-tier-data", sid)

    grp_jobs = add(
        "alma-grp-jobs",
        "Grouping",
        "Задания Kubernetes",
        "Плановые и разовые задания кластера.",
        **vc,
    )
    rel("AggregationRelationship", node, grp_jobs)
    cron = add(
        "alma-tp-cronjobs",
        "TechnologyProcess",
        "CronJob: stuck-check, idempotency-purge, purge-deleted",
        "stuck-check — ежечасно, idempotency-purge — ежечасно со сдвигом, purge-deleted — "
        "ночью; вызывают служебные эндпоинты ядра с X-Internal-Token. Шаблон demo-reset "
        "приостановлен и запускается вручную.",
        **vc,
        Манифест="deploy/k8s/base/cronjobs",
    )
    jobs = add(
        "alma-tp-jobs",
        "TechnologyProcess",
        "Job: db-migrate, minio-init, seed-demo",
        "Миграции Alembic до head, создание бакетов MinIO, засев демо-данных.",
        **vc,
        Манифест="deploy/k8s/base/jobs",
    )
    for tp in (cron, jobs):
        rel("AssignmentRelationship", node, tp)
        rel("AggregationRelationship", grp_jobs, tp)

    grp_images = add(
        "alma-grp-images",
        "Grouping",
        "Образы контейнеров",
        "Собственные образы; собираются из Dockerfile каталога компонента.",
        **vc,
    )
    rel("AggregationRelationship", node, grp_images)
    images = {
        "backend": ("crm/backend", "backend/Dockerfile"),
        "frontend": ("crm/frontend", "frontend/Dockerfile"),
        "notification": ("crm/notification", "services/notification/Dockerfile"),
        "llm-gateway": ("crm/llm-gateway", "services/llm-gateway/Dockerfile"),
        "lms-stub": ("crm/lms-stub", "services/lms-stub/Dockerfile"),
        "cms-stub": ("crm/cms-stub", "services/cms-stub/Dockerfile"),
    }
    for key, (name, dockerfile) in images.items():
        aid = add(
            f"alma-art-{key}",
            "Artifact",
            name,
            "Образ контейнера компонента.",
            **vc,
            Dockerfile=dockerfile,
        )
        rel("AssignmentRelationship", node, aid)
        rel("AggregationRelationship", grp_images, aid)
        rel("RealizationRelationship", aid, f"alma-ac-{key}", "образ")

    grp_constraints = add(
        "alma-grp-constraints",
        "Grouping",
        "Условия и ограничения контура",
        "Архитектурные ограничения, которые соблюдает развёртывание.",
        **vc,
    )
    constraints = {
        "no-egress": (
            "Ядро без выхода в интернет",
            "Default-deny NetworkPolicy, разрешения точка-точка между подами "
            "(deploy/k8s/base/networkpolicy).",
            node,
        ),
        "single-egress": (
            "Выход наружу — только DMZ-поды",
            "notification-service — 443 к Telegram, llm-gateway — 443 и 9443 к GigaChat; токен "
            "бота и ключ GigaChat хранятся только в своих подах; с ядром — HTTP точка-точка.",
            "alma-ac-notification",
        ),
        "ai-facts": (
            "В GigaChat — только агрегаты и псевдонимы",
            "ФИО, вузы, клиенты B2C, заголовки заявок и комментарии наружу не уходят; числа "
            "ответа сверяются с отчётом кодом, советы удаляются.",
            "alma-mod-ai-summary",
        ),
        "stubs": (
            "LMS и CMS на стенде — заглушки",
            "lms-stub и cms-stub говорят с ядром тем же JSON-конвертом с HMAC, что и "
            "реальные системы.",
            "alma-ac-lms-stub",
        ),
        "cache": (
            "KeyDB без персистентности",
            "Сбой кэша деградирует в чтение из PostgreSQL.",
            "alma-sw-keydb",
        ),
        "pii": (
            "ПДн студентов — шифртекст",
            "Fernet на уровне приложения и blind index HMAC для поиска (152-ФЗ).",
            "alma-sw-postgres",
        ),
    }
    for key, (name, doc, target) in constraints.items():
        cid = add(f"alma-con-{key}", "Constraint", name, doc, **vc)
        rel("AggregationRelationship", grp_constraints, cid)
        rel("AssociationRelationship", cid, target)
    rel("AssociationRelationship", "alma-con-stubs", "alma-ac-cms-stub")
    rel("AssociationRelationship", "alma-con-single-egress", "alma-ac-llm-gateway")

    # взаимодействия
    rel("ServingRelationship", "alma-ac-frontend", users, "SPA в браузере")
    for target, name in (
        ("frontend", "/"),
        ("backend", "/api"),
        ("keycloak", "id.*"),
        ("lms-stub", "lms.*"),
        ("cms-stub", "cms.*"),
    ):
        rel("ServingRelationship", ingress, f"alma-ac-{target}", name)
    rel("ServingRelationship", ingress, "alma-sw-minio", "minio.* (консоль)")
    rel("ServingRelationship", "alma-ac-backend", "alma-ac-frontend", "REST /api/v1, SSE")
    for target, name in (
        ("frontend", "OIDC + PKCE"),
        ("backend", "JWKS"),
        ("notification", "client credentials"),
    ):
        rel("ServingRelationship", "alma-ac-keycloak", f"alma-ac-{target}", name)
    rel(
        "FlowRelationship",
        "alma-ac-backend",
        "alma-ac-notification",
        "POST /api/v1/send, X-Internal-Token",
    )
    rel(
        "ServingRelationship",
        "alma-ac-backend",
        "alma-ac-notification",
        "/internal/bot/* — права привязанного",
    )
    for stub in ("lms", "cms"):
        rel(
            "FlowRelationship",
            "alma-ac-backend",
            f"alma-ac-{stub}-stub",
            "события CRM: JSON + HMAC, outbox",
        )
        rel(
            "FlowRelationship",
            f"alma-ac-{stub}-stub",
            "alma-ac-backend",
            f"вебхук /integrations/{stub}/webhook",
        )
        rel(
            "AssociationRelationship",
            f"alma-ac-{stub}-stub",
            f"alma-ext-{stub}",
            "заменяет на стенде",
        )
    rel("FlowRelationship", "alma-ac-notification", "alma-ext-telegram", "443, long-polling")
    rel("FlowRelationship", "alma-ac-notification", "alma-ext-channels", "адаптеры-заглушки")
    rel(
        "FlowRelationship",
        "alma-ac-backend",
        "alma-ac-llm-gateway",
        "сводки ИИ: POST /v1/chat, X-Internal-Token",
    )
    rel("FlowRelationship", "alma-ac-llm-gateway", "alma-ext-gigachat", "OAuth 9443, chat 443")
    rel("ServingRelationship", "alma-sw-postgres", "alma-ac-backend", "БД crm")
    rel("ServingRelationship", "alma-sw-postgres", "alma-ac-keycloak", "БД keycloak")
    rel("ServingRelationship", "alma-sw-keydb", "alma-ac-backend", "кэш, pub/sub SSE")
    rel("ServingRelationship", "alma-sw-keydb", "alma-ac-notification", "идемпотентность")
    rel("ServingRelationship", "alma-sw-minio", "alma-ac-backend", "файлы, импорты, выгрузки")
    rel("TriggeringRelationship", cron, "alma-ac-backend", "POST /internal/jobs/*")
    rel("AssociationRelationship", jobs, "alma-sw-postgres", "миграции, засев")
    rel("AssociationRelationship", jobs, "alma-sw-minio", "бакеты")


# --- проверки --------------------------------------------------------------------------------

_ID_RE = re.compile(r"^[a-zA-Z0-9._-]+$")  # CSVImporter.checkIDForInvalidCharacters


def validate(model: Model, rbac: ModuleType) -> None:
    codes = set(rbac.PERMISSION_CODES)
    for el in model.elements.values():
        if not _ID_RE.match(el.id):
            raise ValueError(f"id {el.id}: Archi допускает только [a-zA-Z0-9._-]")
        if el.type not in FOLDER_OF_TYPE:
            raise ValueError(f"{el.id}: тип {el.type} не описан в FOLDER_OF_TYPE")
        for key in ("Права", "Права функции"):
            for code in filter(None, (c.strip() for c in _prop(el, key).split(","))):
                if code != "все роли" and code not in codes:
                    raise ValueError(f"{el.id}: права {code} нет в каталоге rbac_catalog.py")
    for fn in FUNCTIONS:
        for code in fn.acting:
            if code != ALL_ROLES and code not in codes:
                raise ValueError(f"функция {fn.key}: права {code} нет в каталоге")
    for r in model.relations.values():
        for end in (r.source, r.target):
            if end not in model.elements:
                raise ValueError(f"связь {r.id}: нет элемента {end}")
        pair = (model.elements[r.source].type, model.elements[r.target].type)
        letters = ALLOWED_RELATIONS.get(pair)
        if letters is None or RELATION_LETTERS[r.type] not in letters:
            raise ValueError(f"связь {r.id}: {r.type} {pair[0]} → {pair[1]} недопустима в Archi")
    for view in model.views:
        seen: set[str] = set()
        for box, _parent in walk(view.boxes):
            if box.element not in model.elements:
                raise ValueError(f"вид {view.name}: нет элемента {box.element}")
            if box.element in seen:
                raise ValueError(f"вид {view.name}: {box.element} размещён дважды")
            seen.add(box.element)
        expected = {el.id for el in model.elements.values() if view.name in _prop(el, PROP_VIEW)}
        if seen != expected:
            raise ValueError(
                f"вид {view.name}: без координат {sorted(expected - seen)}, "
                f"лишние {sorted(seen - expected)}"
            )
        _check_geometry(view.name, view.boxes)


def _check_geometry(view_name: str, boxes: list[Box], parent: Box | None = None) -> None:
    """Вложенный объект целиком внутри родителя, соседние объекты не перекрываются."""
    for i, box in enumerate(boxes):
        if parent and (
            box.x < 0 or box.y < 0 or box.x + box.w > parent.w or box.y + box.h > parent.h
        ):
            raise ValueError(f"вид {view_name}: {box.element} выходит за {parent.element}")
        for other in boxes[i + 1 :]:
            if (
                box.x < other.x + other.w
                and other.x < box.x + box.w
                and box.y < other.y + other.h
                and other.y < box.y + box.h
            ):
                raise ValueError(f"вид {view_name}: {box.element} перекрывает {other.element}")
        _check_geometry(view_name, box.children, box)


def walk(boxes: Iterable[Box], parent: Box | None = None) -> Iterator[tuple[Box, Box | None]]:
    for box in boxes:
        yield box, parent
        yield from walk(box.children, box)


# --- раскладка видов -------------------------------------------------------------------------

COL_W = 210  # ширина колонки функциональной схемы
COL_STEP = 220
SVC_W, SVC_H, SVC_STEP = 190, 55, 65
LEFT = 20


def layout_functional(model: Model) -> View:
    width = COL_STEP * len(SERVICE_GROUPS) - (COL_STEP - COL_W)
    role_ids = [f"alma-role-{c.replace('_', '-')}" for c in ROLE_ORDER] + ["alma-role-custom"]
    roles_box = Box(
        "alma-grp-roles",
        LEFT,
        90,
        len(role_ids) * 215 + 10,
        100,
        [Box(rid, 15 + i * 215, 32, 200, 55) for i, rid in enumerate(role_ids)],
    )
    actors_x = LEFT + roles_box.w + 20
    actors_box = Box(
        "alma-grp-actors",
        actors_x,
        90,
        LEFT + width - actors_x,
        100,
        [
            Box(f"alma-actor-{key}", 15 + i * 240, 32, 220, 55)
            for i, (key, *_rest) in enumerate(EXTERNAL_ACTORS)
        ],
    )
    col_index = {key: i for i, (key, _) in enumerate(SERVICE_GROUPS)}
    slots: dict[str, int] = {}
    fn_boxes = []
    for fn in FUNCTIONS:
        col = col_index[fn.column]
        slot = slots.get(fn.column, 0)
        slots[fn.column] = slot + 1
        fn_boxes.append(Box(f"alma-fn-{fn.key}", col * COL_STEP + 10, 32 + slot * 68, 190, 58))
    if max(slots.values()) > 2:
        raise ValueError("в колонке функциональной схемы больше двух функций")
    fn_box = Box("alma-grp-functions", LEFT, 230, width, 170, fn_boxes)
    groups = []
    for key, _name in SERVICE_GROUPS:
        members = [s for s in SERVICES if s.group == key]
        children = [
            Box(f"alma-svc-{s.key}", 10, 32 + k * SVC_STEP, SVC_W, SVC_H)
            for k, s in enumerate(members)
        ]
        groups.append(
            Box(
                f"alma-grp-svc-{key}",
                LEFT + col_index[key] * COL_STEP,
                440,
                COL_W,
                32 + len(members) * SVC_STEP + 5,
                children,
            )
        )
    note = (
        "Функциональная схема «Альмы»: роли выполняют бизнес-функции (назначения рассчитаны "
        "по каталогу прав backend/app/core/rbac_catalog.py), прикладные сервисы под "
        "функциями их обслуживают. Свойства сервисов — API, права и экран. "
        f"Сгенерировано: {GENERATOR}."
    )
    return View(
        "vf",
        VIEW_FUNCTIONAL,
        note,
        (LEFT, 20, width, 52),
        [roles_box, actors_box, fn_box, *groups],
    )


def layout_component(model: Model) -> View:
    width = 1760
    users = Box("alma-actor-users", LEFT, 90, 240, 55)
    ext_keys = ("lms", "cms", "telegram", "channels", "gigachat")
    ext = Box(
        "alma-grp-external",
        700,
        80,
        LEFT + width - 700,
        100,
        [Box(f"alma-ext-{k}", 20 + i * 210, 32, 195, 55) for i, k in enumerate(ext_keys)],
    )
    modules = [
        Box(_module_id(key), 15 + (i % 5) * 176, 40 + (i // 5) * 68, 165, 55)
        for i, (key, *_rest) in enumerate(MODULES)
    ]
    backend = Box("alma-ac-backend", 20, 110, 905, 320, modules)
    tier_app = Box(
        "alma-grp-tier-app",
        20,
        40,
        1180,
        450,
        [
            Box("alma-sw-ingress", 20, 35, 220, 55),
            Box("alma-ac-frontend", 280, 35, 260, 55),
            backend,
            Box("alma-ac-lms-stub", 960, 110, 200, 55),
            Box("alma-ac-cms-stub", 960, 200, 200, 55),
        ],
    )
    tier_iam = Box(
        "alma-grp-tier-iam", 1220, 40, 250, 100, [Box("alma-ac-keycloak", 20, 32, 210, 55)]
    )
    tier_dmz = Box(
        "alma-grp-tier-dmz",
        1490,
        40,
        250,
        170,
        [
            Box("alma-ac-notification", 20, 32, 210, 55),
            Box("alma-ac-llm-gateway", 20, 100, 210, 55),
        ],
    )
    tier_data = Box(
        "alma-grp-tier-data",
        1220,
        230,
        520,
        100,
        [
            Box(f"alma-sw-{k}", 15 + i * 168, 32, 158, 55)
            for i, k in enumerate(("postgres", "keydb", "minio"))
        ],
    )
    jobs = Box(
        "alma-grp-jobs",
        1220,
        350,
        520,
        110,
        [Box("alma-tp-cronjobs", 15, 32, 240, 65), Box("alma-tp-jobs", 265, 32, 240, 65)],
    )
    image_keys = ("backend", "frontend", "notification", "llm-gateway", "lms-stub", "cms-stub")
    images = Box(
        "alma-grp-images",
        20,
        510,
        1720,
        100,
        [Box(f"alma-art-{k}", 20 + i * 220, 32, 200, 55) for i, k in enumerate(image_keys)],
    )
    node = Box(
        "alma-node-k8s",
        LEFT,
        220,
        width,
        630,
        [tier_app, tier_iam, tier_dmz, tier_data, jobs, images],
    )
    con_keys = ("no-egress", "single-egress", "stubs", "cache", "pii", "ai-facts")
    constraints = Box(
        "alma-grp-constraints",
        LEFT,
        880,
        width,
        100,
        [Box(f"alma-con-{k}", 20 + i * 288, 32, 272, 55) for i, k in enumerate(con_keys)],
    )
    note = (
        "Компонентная схема «Альмы»: модули backend, сервисы-сателлиты, хранилища и "
        "Keycloak в кластере Kubernetes (namespace crm), зоны tier по меткам манифестов, "
        "образы и задания. Подписи связей — протоколы и маршруты. "
        f"Сгенерировано: {GENERATOR}."
    )
    return View("vc", VIEW_COMPONENT, note, (LEFT, 20, width, 52), [users, ext, node, constraints])


# --- связи на видах --------------------------------------------------------------------------


def view_connections(model: Model, view: View) -> list[Relation]:
    """Связи вида: оба конца на виде и ни один не вложен в другой (вложенность их заменяет)."""
    ancestors: dict[str, set[str]] = {}

    def collect(boxes: list[Box], chain: set[str]) -> None:
        for box in boxes:
            ancestors[box.element] = set(chain)
            collect(box.children, chain | {box.element})

    collect(view.boxes, set())
    result = []
    for r in model.relations.values():
        if r.source not in ancestors or r.target not in ancestors:
            continue
        if r.source in ancestors[r.target] or r.target in ancestors[r.source]:
            continue
        result.append(r)
    return result


# --- вывод: CSV ------------------------------------------------------------------------------


def _csv(rows: Iterable[Iterable[str]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\n")
    writer.writerows(rows)
    return buf.getvalue()


def render_csv(model: Model) -> dict[str, str]:
    elements = [("ID", "Type", "Name", "Documentation", "Specialization")]
    elements.append((MODEL_ID, "ArchimateModel", MODEL_NAME, MODEL_PURPOSE, ""))
    elements += [(e.id, e.type, e.name, e.doc, "") for e in model.elements.values()]
    relations = [("ID", "Type", "Name", "Documentation", "Source", "Target", "Specialization")]
    relations += [
        (r.id, r.type, r.name, "", r.source, r.target, "") for r in model.relations.values()
    ]
    properties = [("ID", "Key", "Value")]
    properties += [(MODEL_ID, key, value) for key, value in model_properties()]
    for el in model.elements.values():
        properties += [(el.id, key, value) for key, value in el.props]
    return {
        "elements.csv": _csv(elements),
        "relations.csv": _csv(relations),
        "properties.csv": _csv(properties),
    }


def model_properties() -> list[tuple[str, str]]:
    return [
        ("Нотация", "ArchiMate 3.1"),
        ("Генератор", GENERATOR),
        ("Каталог прав", "backend/app/core/rbac_catalog.py"),
        ("Виды", f"{VIEW_FUNCTIONAL}; {VIEW_COMPONENT}"),
    ]


# --- вывод: .archimate -----------------------------------------------------------------------


def _attr(value: str) -> str:
    return escape(value, {'"': "&quot;", "\n": "&#xA;", "\r": "&#xD;", "\t": "&#x9;"})


def _element_xml(el: Element, indent: str) -> list[str]:
    head = f'{indent}<element xsi:type="archimate:{el.type}" name="{_attr(el.name)}" id="{el.id}"'
    if not el.doc and not el.props:
        return [head + "/>"]
    lines = [head + ">"]
    if el.doc:
        lines.append(f"{indent}  <documentation>{escape(el.doc)}</documentation>")
    lines += [f'{indent}  <property key="{_attr(k)}" value="{_attr(v)}"/>' for k, v in el.props]
    lines.append(f"{indent}</element>")
    return lines


def render_archimate(model: Model) -> str:
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<archimate:model xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:archimate="http://www.archimatetool.com/archimate" '
        f'name="{_attr(MODEL_NAME)}" id="{MODEL_ID}" version="{ARCHI_MODEL_VERSION}">',
    ]
    for folder_type, folder_name in FOLDERS:
        folder_id = f"alma-folder-{folder_type.replace('_', '-')}"
        if folder_type == "relations":
            items = [line for r in model.relations.values() for line in _relation_xml(r, "    ")]
        elif folder_type == "diagrams":
            items = [line for view in model.views for line in _view_xml(model, view, "    ")]
        else:
            items = [
                line
                for el in model.elements.values()
                if FOLDER_OF_TYPE[el.type] == folder_type
                for line in _element_xml(el, "    ")
            ]
        head = f'  <folder name="{_attr(folder_name)}" id="{folder_id}" type="{folder_type}"'
        if items:
            out += [head + ">", *items, "  </folder>"]
        else:
            out.append(head + "/>")
    out.append(f"  <purpose>{escape(MODEL_PURPOSE)}</purpose>")
    out += [f'  <property key="{_attr(k)}" value="{_attr(v)}"/>' for k, v in model_properties()]
    out.append("</archimate:model>")
    return "\n".join(out) + "\n"


def _relation_xml(r: Relation, indent: str) -> list[str]:
    name = f' name="{_attr(r.name)}"' if r.name else ""
    return [
        f'{indent}<element xsi:type="archimate:{r.type}"{name} id="{r.id}" '
        f'source="{r.source}" target="{r.target}"/>'
    ]


def _dmo_id(view: View, element_id: str) -> str:
    return f"alma-{view.key}-{_short(element_id)}"


def _view_xml(model: Model, view: View, indent: str) -> list[str]:
    connections = view_connections(model, view)
    outgoing: dict[str, list[Relation]] = {}
    incoming: dict[str, list[str]] = {}
    for r in connections:
        outgoing.setdefault(r.source, []).append(r)
        incoming.setdefault(r.target, []).append(f"alma-{view.key}-c-{_short(r.id)}")

    lines = [
        f'{indent}<element xsi:type="archimate:ArchimateDiagramModel" '
        f'name="{_attr(view.name)}" id="alma-view-{view.key}">'
    ]
    x, y, w, h = view.note_box
    lines += [
        f'{indent}  <child xsi:type="archimate:Note" id="alma-{view.key}-note">',
        f'{indent}    <bounds x="{x}" y="{y}" width="{w}" height="{h}"/>',
        f"{indent}    <content>{escape(view.note)}</content>",
        f"{indent}  </child>",
    ]

    def emit(box: Box, pad: str) -> None:
        targets = incoming.get(box.element)
        tc = f' targetConnections="{" ".join(targets)}"' if targets else ""
        lines.append(
            f'{pad}<child xsi:type="archimate:DiagramObject" id="{_dmo_id(view, box.element)}"'
            f'{tc} archimateElement="{box.element}">'
        )
        lines.append(f'{pad}  <bounds x="{box.x}" y="{box.y}" width="{box.w}" height="{box.h}"/>')
        for r in outgoing.get(box.element, []):
            lines.append(
                f'{pad}  <sourceConnection xsi:type="archimate:Connection" '
                f'id="alma-{view.key}-c-{_short(r.id)}" source="{_dmo_id(view, r.source)}" '
                f'target="{_dmo_id(view, r.target)}" archimateRelationship="{r.id}"/>'
            )
        for child in box.children:
            emit(child, pad + "  ")
        lines.append(f"{pad}</child>")

    for box in view.boxes:
        emit(box, indent + "  ")
    lines.append(f"{indent}</element>")
    return lines


# --- вывод: Mermaid и сводка для README ------------------------------------------------------


def _mid(element_id: str) -> str:
    return _short(element_id).replace("-", "_")


def _mlabel(text: str) -> str:
    return text.replace('"', "#quot;")


_SHAPES = {
    "BusinessActor": ('(["', '"])'),
    "BusinessRole": ('(["', '"])'),
    "SystemSoftware": ('[("', '")]'),
    "TechnologyProcess": ('[["', '"]]'),
    "Constraint": ('>"', '"]'),
}
_ARROWS = {
    "ServingRelationship": "-->",
    "AssignmentRelationship": "-->",
    "FlowRelationship": "-.->",
    "TriggeringRelationship": "==>",
    "RealizationRelationship": "-.->",
    "AssociationRelationship": "---",
    "SpecializationRelationship": "-->",
}


def _mermaid_nodes(model: Model, boxes: list[Box], pad: str, out: list[str]) -> None:
    for box in boxes:
        el = model.elements[box.element]
        if box.children:
            out.append(f'{pad}subgraph {_mid(el.id)}["{_mlabel(el.name)}"]')
            _mermaid_nodes(model, box.children, pad + "    ", out)
            out.append(f"{pad}end")
        else:
            left, right = _SHAPES.get(el.type, ('["', '"]'))
            out.append(f"{pad}{_mid(el.id)}{left}{_mlabel(el.name)}{right}")


def _mermaid_edge(r: Relation, source: str | None = None) -> str:
    arrow = _ARROWS[r.type]
    label = r.name or ("специализация" if r.type == "SpecializationRelationship" else "")
    src = source or _mid(r.source)
    if label:
        return f'    {src} {arrow}|"{_mlabel(label)}"| {_mid(r.target)}'
    return f"    {src} {arrow} {_mid(r.target)}"


def render_mermaid(model: Model, view: View) -> str:
    """Превью вида для GitHub. Функциональная схема — слева направо: роли → функции ← группы
    сервисов (одна линия на пару «группа — функция»); компонентная — сверху вниз, модули
    backend невидимыми связями уложены в сетку по пять."""
    functional = view.key == "vf"
    out = ["```mermaid", "flowchart LR" if functional else "flowchart TB"]
    _mermaid_nodes(model, view.boxes, "    ", out)
    seen: set[str] = set()
    group_of = {f"alma-svc-{s.key}": f"alma-grp-svc-{s.group}" for s in SERVICES}
    for r in view_connections(model, view):
        if r.type == "SpecializationRelationship":
            continue  # роли одной колонки: связь сломала бы раскладку превью
        if functional and r.type == "ServingRelationship" and r.source in group_of:
            edge = f"    {_mid(r.target)} -.- {_mid(group_of[r.source])}"
        else:
            edge = _mermaid_edge(r)
        if edge not in seen:
            seen.add(edge)
            out.append(edge)
    if not functional:
        mods = [_mid(_module_id(key)) for key, *_rest in MODULES]
        out += [f"    {a} ~~~ {b}" for a, b in zip(mods, mods[5:], strict=False)]
    out.append("```")
    return "\n".join(out)


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} {one}"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} {few}"
    return f"{n} {many}"


def render_stats(model: Model) -> str:
    lines = ["| Вид | Элементов | Связей на виде | Состав |", "|---|---:|---:|---|"]
    for view in model.views:
        ids = [box.element for box, _ in walk(view.boxes)]
        by_type: dict[str, int] = {}
        for el_id in ids:
            t = model.elements[el_id].type
            by_type[t] = by_type.get(t, 0) + 1
        parts = ", ".join(
            f"{label} — {by_type[t]}" for t, label in TYPE_LABELS.items() if t in by_type
        )
        lines.append(
            f"| {view.name} | {len(ids)} | {len(view_connections(model, view))} | {parts} |"
        )
    shown = {r.id for view in model.views for r in view_connections(model, view)}
    hidden = len(model.relations) - len(shown)
    lines += [
        "",
        f"Всего в модели {_plural(len(model.elements), 'элемент', 'элемента', 'элементов')} "
        f"и {_plural(len(model.relations), 'связь', 'связи', 'связей')}. Связей без линии на "
        f"видах — {hidden}: вложенность (группировка, композиция "
        "модулей в backend, размещение в кластере) и трассировка «компонент реализует "
        "прикладной сервис», которая соединяет два вида. Эти связи видны в дереве модели и на "
        "вкладке Analysis → Model Relations свойств элемента.",
    ]
    rel_counts: dict[str, int] = {}
    for r in model.relations.values():
        rel_counts[r.type] = rel_counts.get(r.type, 0) + 1
    lines += ["", "| Тип связи | Связей |", "|---|---:|"]
    lines += [f"| {RELATION_LABELS[t]} (`{t}`) | {n} |" for t, n in sorted(rel_counts.items())]
    return "\n".join(lines)


_MARKER_RE = re.compile(
    r"(<!-- archi:(?P<name>[a-z]+):begin -->\n)(?P<body>.*?)(<!-- archi:(?P=name):end -->)",
    re.S,
)


def update_readme(text: str, blocks: dict[str, str]) -> str:
    found: set[str] = set()

    def repl(match: re.Match[str]) -> str:
        name = match.group("name")
        if name not in blocks:
            return match.group(0)
        found.add(name)
        return f"{match.group(1)}{blocks[name]}\n{match.group(4)}"

    result = _MARKER_RE.sub(repl, text)
    missing = set(blocks) - found
    if missing:
        raise ValueError(f"README.md: нет маркеров {sorted(missing)}")
    return result


# --- точка входа -----------------------------------------------------------------------------


def render_all(model: Model, readme_text: str) -> dict[str, str]:
    outputs = render_csv(model)
    outputs["alma.archimate"] = render_archimate(model)
    by_key = {view.key: view for view in model.views}
    outputs["README.md"] = update_readme(
        readme_text,
        {
            "stats": render_stats(model),
            "functional": render_mermaid(model, by_key["vf"]),
            "component": render_mermaid(model, by_key["vc"]),
        },
    )
    return outputs


def generate() -> tuple[Model, dict[str, str]]:
    rbac = load_rbac_catalog()
    model = build_model(rbac)
    model.views = [layout_functional(model), layout_component(model)]
    validate(model, rbac)
    readme = (HERE / "README.md").read_text(encoding="utf-8")
    return model, render_all(model, readme)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="только проверить актуальность")
    args = parser.parse_args(argv)
    _model, outputs = generate()
    stale = []
    for name, content in outputs.items():
        path = HERE / name
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == content:
            continue
        stale.append(name)
        if not args.check:
            path.write_text(content, encoding="utf-8")
    if args.check and stale:
        sys.stderr.write(f"устарели: {', '.join(stale)} — запустите python3 {GENERATOR}\n")
        return 1
    sys.stdout.write(f"{'обновлены' if stale else 'актуальны'}: {', '.join(stale or outputs)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
