"""Идемпотентный seed демо-стенда (`python -m app.tools.seed`, обёртка — `python -m app.seed`).

Наполняет (data-model.md §11, deployment.md §3.8 Job `seed-demo`):
- обе воронки строго по api-contract.md §5.1 (JSON — app/modules/workflow/seeds/);
- справочник interaction_type, app_setting, дефолтные notification_rule;
- демо-пользователей (зеркала realm-пользователей, manager_id: kam → head_kam);
- каталог прав и системные роли (сверка с кодом) и демо-роли «КАМ B2B» / «КАМ B2C»
  (права КАМа в одном процессе), вписанные в переходы своих процессов;
- справочник ИТ-направлений (DevOps, QA, анализ данных…) с привязкой программ (одно
  направление) и продуктов (несколько направлений);
- демо-домен: вузы, контакты, продукты, программы (с приоритетами), договоры
  («1 договор — N продуктов»; лицензии в строках договора — часть истекает в ближайшие
  30 дней, одна истекла), заявки B2B/B2C с историей (включая заведомо зависшие — фактура
  для демо CronJob — и нераспределённые лиды CMS у руководителя), студентов Talent Pool
  (ПДн шифруются тем же кодом, что в проде), федпроекты и активности с расписанием;
- историю активности ~20 КАМов для отчёта «Активность КАМов» (`app.tools.seed_activity`:
  комментарии, файлы заявок, прошедшие мероприятия, исторические заявки);
- просрочки (`app.tools.seed_sla`): сценарные заявки с разной картиной просрочек, передача
  портфеля, журнал напоминаний о зависших и реакции КАМов на них;
- скрытый онбординг демо-учёток (приветствие пройдено, чек-лист свёрнут).

Идемпотентность — upsert по натуральным ключам: повторный запуск ничего не дублирует.

Все даты считаются от «сейчас» демо-данных: момент запуска либо `DEMO_ANCHOR_DATE`
(ГГГГ-ММ-ДД — 09:00 МСК этого дня, или ISO дата-время). Идемпотентный повтор даты уже
созданных строк не двигает, поэтому к показу стенд пересевают с нуля:

    python -m app.tools.seed --reset [--anchor 2026-10-23]   # make demo-reset

Сброс (`--reset`) в одной транзакции очищает доменные таблицы (заявки, история, вузы,
каталог, договоры, студенты, мероприятия, файлы, уведомления, импорт, этапы и переходы
процессов) и засевает их заново от якоря. Не очищаются `app_user` (keycloak_sub, привязки
Telegram), каталог ролей и прав, настройки (кроме ключей APP_SETTINGS), строки процессов
и журнал аудита; роли приводятся к исходным: системные — к набору из кода, демо-роли
«КАМ B2B»/«КАМ B2C» — к правам КАМа, прочие пользовательские роли удаляются, демо-учёткам
возвращаются роли сида. Файлы удалённых строк стираются из MinIO, кэши отчётов и схем
в KeyDB сбрасываются. Названия вузов — реальные; заявки, договоры, этапы и комментарии —
демонстрационные и не описывают действительных отношений «ИТ Школы» с этими вузами.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import reset_role_catalog
from app.core.db import dispose_engine, get_session_factory
from app.core.pii import (
    display_name_from_full_name,
    get_pii_crypto,
)
from app.models import (
    Activity,
    ActivitySchedule,
    AppRole,
    AppRolePermission,
    AppSetting,
    AppUser,
    Contract,
    ContractProduct,
    Counterparty,
    Deal,
    DealStageHistory,
    FederalProject,
    InteractionType,
    ItDirection,
    NotificationRule,
    Product,
    ProductDirection,
    Program,
    Student,
    StudentActivity,
    StudentStatusHistory,
    University,
    UniversityContact,
    Workflow,
    WorkflowStage,
    WorkflowTransition,
)
from app.modules.import_export.importers import license_end

SEEDS_DIR = Path(__file__).resolve().parents[1] / "modules" / "workflow" / "seeds"

MSK = timezone(timedelta(hours=3), "MSK")
DEMO_ANCHOR_ENV = "DEMO_ANCHOR_DATE"
DEMO_ANCHOR_HOUR_MSK = 9  # якорь-дата без времени — начало дня показа


def resolve_demo_anchor(value: str | None, *, now: datetime | None = None) -> datetime:
    """«Сейчас» демо-данных (UTC): пусто — текущий момент; `ГГГГ-ММ-ДД` — 09:00 МСК
    этого дня (для сегодняшней даты — не позже текущего момента); ISO дата-время —
    как есть (без зоны — МСК)."""
    current = now or datetime.now(timezone.utc)
    raw = (value or "").strip()
    if not raw:
        return current
    try:
        if len(raw) == 10:
            day = date.fromisoformat(raw)
            moment = datetime(day.year, day.month, day.day, DEMO_ANCHOR_HOUR_MSK, tzinfo=MSK)
            if day == current.astimezone(MSK).date():
                moment = min(moment, current)
        else:
            moment = datetime.fromisoformat(raw)
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=MSK)
    except ValueError as exc:
        raise ValueError(
            f"{DEMO_ANCHOR_ENV}=«{raw}»: ожидается ГГГГ-ММ-ДД или ISO дата-время"
        ) from exc
    return moment.astimezone(timezone.utc)


_NOW = resolve_demo_anchor(os.environ.get(DEMO_ANCHOR_ENV))


def demo_now() -> datetime:
    """Текущий якорь сида (читать через функцию: `--anchor` переопределяет его)."""
    return _NOW


def set_demo_now(moment: datetime) -> None:
    global _NOW
    _NOW = moment


def _demo_sub(username: str) -> uuid.UUID:
    """Детерминированный keycloak_sub для сида; при первом логине строка «усыновляется»
    по username и sub заменяется настоящим (app.core.security.sync_app_user)."""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"https://crm.local/demo-user/{username}")


# --------------------------------------------------------------------------------------
# Демо-данные (обезличенные; ФИО/email студентов и физлиц шифруются при записи)
# --------------------------------------------------------------------------------------

DEMO_USERS: list[dict[str, Any]] = [
    {
        "username": "admin@demo",
        "full_name": "Соколова Анна Викторовна",
        "email": "admin@crm.local",
        "roles": ["admin"],
    },
    {
        "username": "head@demo",
        "full_name": "Морозов Дмитрий Александрович",
        "email": "head@crm.local",
        "roles": ["head_kam"],
    },
    {
        "username": "kam1@demo",
        "full_name": "Петров Константин Андреевич",
        "email": "kam1@crm.local",
        "roles": ["kam"],
        "manager": "head@demo",
    },
    {
        "username": "kam2@demo",
        "full_name": "Иванова Мария Сергеевна",
        "email": "kam2@crm.local",
        "roles": ["kam"],
        "manager": "head@demo",
    },
    {
        "username": "observer@demo",
        "full_name": "Кузнецов Олег Игоревич",
        "email": "observer@crm.local",
        "roles": ["observer"],
    },
]

INTERACTION_TYPES = [
    ("dpo_course", "Обучение на курсе ДПО", "Физлицо приобретает курс ДПО", 10),
    ("internship", "Стажировка", "Стажировка студента или выпускника", 20),
    ("corporate_training", "Корпоративное обучение", "Обучение сотрудников юрлица", 30),
    ("event", "Участие в мероприятии", "Конференции, олимпиады, дни открытых дверей", 40),
]

APP_SETTINGS = [
    ("stuck_threshold_days_default", {"value": 14}, "Глобальный порог «зависания», дни (1–60)"),
    ("stuck_check_enabled", {"value": True}, "Включена ли проверка зависших заявок"),
    (
        "stuck_escalate_to_manager",
        {"value": True},
        "Эскалация зависших руководителю (manager_id) при 1.5×порога",
    ),
    (
        "feature_flags",
        {"auto_ranking": False, "report_pdf": True, "ai_summaries": True},
        "Фичефлаги приложения (реестр — app.modules.admin.service.KNOWN_FLAGS)",
    ),
    (
        "integration_mode",
        {"value": "http"},
        "Транспорт интеграций: http (outbox PostgreSQL → HTTP + HMAC)",
    ),
    ("file_delivery", {"value": "proxy"}, "Выдача файлов: proxy | presigned"),
    (
        "notification_quiet_hours",
        {"value": {"from": "21:00", "to": "09:00", "tz": "Europe/Moscow"}},
        "Тихие часы нотификаций (зарезервировано, в MVP не применяется)",
    ),
]

NOTIFICATION_RULES: list[dict[str, Any]] = [
    {
        "event_type": "request.stuck",
        "recipient_type": "responsible",
        "channel": "telegram",
        "params": {"repeat_every_days": 3},
    },
    {
        "event_type": "request.stuck",
        "recipient_type": "manager_of_responsible",
        "channel": "telegram",
        "params": {"repeat_every_days": 3},
    },
    {"event_type": "request.transitioned", "recipient_type": "responsible", "channel": "telegram"},
    {"event_type": "request.comment_added", "recipient_type": "responsible", "channel": "telegram"},
    {
        "event_type": "workflow.change_requested",
        "recipient_type": "role",
        "recipient_value": "admin",
        "channel": "telegram",
    },
    {
        "event_type": "integration.lead_received",
        "recipient_type": "role",
        "recipient_value": "head_kam",
        "channel": "telegram",
    },
    {
        "event_type": "student.talent_pool_added",
        "recipient_type": "role",
        "recipient_value": "head_kam",
        "channel": "email",
    },
]

UNIVERSITIES: list[dict[str, Any]] = [
    {
        "name": "Московский физико-технический институт",
        "short_name": "МФТИ",
        "inn": "5008006211",
        "region": "Московская область",
        "city": "Долгопрудный",
        "website": "https://mipt.ru",
        "kam": "kam1@demo",
    },
    {
        "name": "МГТУ им. Н.Э. Баумана",
        "short_name": "Бауманка",
        "inn": "7707082662",
        "region": "Москва",
        "city": "Москва",
        "website": "https://bmstu.ru",
        "kam": "kam1@demo",
    },
    {
        "name": "Санкт-Петербургский политехнический университет Петра Великого",
        "short_name": "СПбПУ",
        "inn": "7804040077",
        "region": "Санкт-Петербург",
        "city": "Санкт-Петербург",
        "website": "https://spbstu.ru",
        "kam": "kam2@demo",
    },
    {
        "name": "Уральский федеральный университет",
        "short_name": "УрФУ",
        "inn": "6660003190",
        "region": "Свердловская область",
        "city": "Екатеринбург",
        "website": "https://urfu.ru",
        "kam": "kam2@demo",
    },
    {
        "name": "Казанский федеральный университет",
        "short_name": "КФУ",
        "inn": "1655018018",
        "region": "Республика Татарстан",
        "city": "Казань",
        "website": "https://kpfu.ru",
        "kam": "kam1@demo",
    },
    {
        "name": "Новосибирский государственный университет",
        "short_name": "НГУ",
        "inn": "5408106490",
        "region": "Новосибирская область",
        "city": "Новосибирск",
        "website": "https://nsu.ru",
        "kam": "kam2@demo",
    },
    {
        "name": "Дальневосточный федеральный университет",
        "short_name": "ДВФУ",
        "inn": "2536014538",
        "region": "Приморский край",
        "city": "Владивосток",
        "website": "https://dvfu.ru",
        "kam": "kam1@demo",
    },
    {
        "name": "Южный федеральный университет",
        "short_name": "ЮФУ",
        "inn": "6163027810",
        "region": "Ростовская область",
        "city": "Ростов-на-Дону",
        "website": "https://sfedu.ru",
        "kam": "kam2@demo",
    },
]

UNIVERSITY_CONTACTS: list[dict[str, str]] = [
    {
        "university": "МФТИ",
        "full_name": "Григорьев Павел Николаевич",
        "position": "Проректор по дополнительному образованию",
        "email": "grigoriev@mipt.ru",
        "phone": "+7 495 408-45-54",
    },
    {
        "university": "Бауманка",
        "full_name": "Лебедева Ирина Владимировна",
        "position": "Начальник управления ДПО",
        "email": "lebedeva@bmstu.ru",
        "phone": "+7 499 263-63-91",
    },
    {
        "university": "СПбПУ",
        "full_name": "Волков Андрей Петрович",
        "position": "Директор центра открытого образования",
        "email": "volkov@spbstu.ru",
        "phone": "+7 812 297-20-95",
    },
]

PRODUCTS = [
    ("dpo-ds", "ДПО: Data Science", "dpo_program", "Программы ДПО по анализу данных"),
    ("dpo-ai", "ДПО: Искусственный интеллект", "dpo_program", "Программы ДПО по ИИ"),
    ("corp-edu", "Корпоративное обучение", "service", "Обучение сотрудников под заказ"),
]

PROGRAMS: list[dict[str, Any]] = [
    {
        "name": "Практикум по ML, весна-2027",
        "product": "dpo-ds",
        "university": "МФТИ",
        "priority_rank": 10,
        "duration_hours": 256,
        "seats": 30,
        "published_to_cms": True,
    },
    {
        "name": "ИИ-инженер, 256 ч.",
        "product": "dpo-ai",
        "university": "Бауманка",
        "priority_rank": 20,
        "duration_hours": 256,
        "seats": 25,
        "published_to_cms": True,
    },
    {
        "name": "Аналитик данных с нуля",
        "product": "dpo-ds",
        "university": None,
        "priority_rank": 30,
        "duration_hours": 144,
        "seats": 50,
        "published_to_cms": True,
    },
    {
        "name": "Компьютерное зрение на производстве",
        "product": "dpo-ai",
        "university": "УрФУ",
        "priority_rank": 40,
        "duration_hours": 120,
        "seats": 20,
        "published_to_cms": False,
    },
    {
        "name": "Корпоративная школа Python",
        "product": "corp-edu",
        "university": None,
        "priority_rank": 50,
        "duration_hours": 72,
        "seats": 40,
        "published_to_cms": False,
    },
    {
        "name": "MLOps для инженеров",
        "product": "dpo-ai",
        "university": "СПбПУ",
        "priority_rank": 60,
        "duration_hours": 96,
        "seats": 15,
        "published_to_cms": True,
    },
]

# справочник ИТ-направлений (код, название, описание) — порядок = sort_order
IT_DIRECTIONS: list[tuple[str, str, str]] = [
    ("data-analysis", "Анализ данных", "Аналитика, статистика, BI и Data Science"),
    ("ml-ai", "Машинное обучение и ИИ", "ML, нейросети, компьютерное зрение, NLP"),
    ("data-engineering", "Инженерия данных", "Хранилища и конвейеры данных, Big Data"),
    ("devops", "DevOps", "CI/CD, контейнеры, эксплуатация сервисов и MLOps"),
    ("qa", "Тестирование (QA)", "Ручное и автоматизированное тестирование ПО"),
    ("backend", "Backend-разработка", "Серверная разработка: Python, Java, Go"),
    ("frontend", "Frontend-разработка", "Веб-интерфейсы: JavaScript, TypeScript, React"),
    ("mobile", "Мобильная разработка", "Приложения для iOS и Android"),
    ("infosec", "Информационная безопасность", "Защита информации, SOC, анализ защищённости"),
    ("system-analysis", "Системный анализ", "Требования, проектирование информационных систем"),
]

# программа ведётся по одному направлению
PROGRAM_DIRECTIONS: dict[str, str] = {
    "Практикум по ML, весна-2027": "ml-ai",
    "ИИ-инженер, 256 ч.": "ml-ai",
    "Аналитик данных с нуля": "data-analysis",
    "Компьютерное зрение на производстве": "ml-ai",
    "Корпоративная школа Python": "backend",
    "MLOps для инженеров": "devops",
}

# ИТ-продукт помогает учить по нескольким направлениям
PRODUCT_DIRECTIONS: dict[str, list[str]] = {
    "dpo-ds": ["data-analysis", "data-engineering", "ml-ai"],
    "dpo-ai": ["ml-ai", "data-analysis", "devops"],
    "corp-edu": ["backend", "frontend", "mobile", "qa", "devops", "infosec", "system-analysis"],
}

# licenses — строки договора как лицензии (миграция 0008): продукт → (сколько дней назад
# подписана, статус передачи вузу); срок — год, последний день — по правилу импорта
# «Таблица КАМов» (`license_end`). Даты — от якоря: к показу 3 лицензии истекают в
# ближайшие 30 дней и 1 истекла у действующего договора (сигналы «Аналитики»)
CONTRACTS: list[dict[str, Any]] = [
    {
        # демонстрация «1 договор — N продуктов»
        "number": "Д-2026/041",
        "university": "МФТИ",
        "status": "active",
        "signed": 220,
        "valid_days": 365,
        "amount": "1200000.00",
        "products": ["dpo-ds", "dpo-ai", "corp-edu"],
        "responsible": "kam1@demo",
        "licenses": {
            "dpo-ds": (220, "transferred"),  # вместе с договором — ещё ~5 месяцев
            "dpo-ai": (356, "transferred"),  # продление прошлого года — истекает через 8 дней
            "corp-edu": (340, "in_progress"),  # истекает через 24 дня
        },
    },
    {
        "number": "Д-2026/052",
        "university": "Бауманка",
        "status": "negotiation",
        "signed": None,
        "valid_days": None,
        "amount": "800000.00",
        "products": ["dpo-ai"],
        "responsible": "kam1@demo",
    },
    {
        # рамочный договор на два года: лицензия Data Science истекла, ИИ — истекает
        "number": "Д-2025/118",
        "university": "ДВФУ",
        "status": "active",
        "signed": 400,
        "valid_days": 730,
        "amount": "650000.00",
        "products": ["dpo-ds", "dpo-ai"],
        "responsible": "kam1@demo",
        "licenses": {
            "dpo-ds": (400, "transferred"),  # истекла 36 дней назад
            "dpo-ai": (350, "transferred"),  # истекает через 14 дней
        },
    },
]

PERSONS: list[dict[str, str]] = [
    {
        "full_name": "Смирнова Ольга Викторовна",
        "email": "smirnova@example.com",
        "phone": "+7 916 000-11-22",
    },
    {
        "full_name": "Ковалёв Артём Дмитриевич",
        "email": "kovalev@example.com",
        "phone": "+7 903 111-22-33",
    },
    {
        "full_name": "Никитина Дарья Павловна",
        "email": "nikitina@example.com",
        "phone": "+7 926 222-33-44",
    },
    {
        "full_name": "Фёдоров Максим Ильич",
        "email": "fedorov@example.com",
        "phone": "+7 915 333-44-55",
    },
    {
        "full_name": "Белова Ксения Андреевна",
        "email": "belova@example.com",
        "phone": "+7 917 444-55-66",
    },
    {
        "full_name": "Громов Илья Сергеевич",
        "email": "gromov@example.com",
        "phone": "+7 925 555-66-77",
    },
]

LEGAL_ENTITIES: list[dict[str, str]] = [
    {"org_name": "ООО «ТехноСофт»", "inn": "7729355614", "org_email": "hr@technosoft.ru"},
    {"org_name": "АО «ПромАвтоматика»", "inn": "6658078880", "org_email": "edu@promauto.ru"},
]

# stage_days — сколько дней заявка стоит на текущем этапе (для демо зависших >14 и >21);
# у закрытых — сколько дней назад закрыта (3–20: KPI периода «месяц» не пустые к показу)
DEALS_B2B: list[dict[str, Any]] = [
    {
        "title": "МФТИ — ДПО Data Science, поток весна-2027",
        "university": "МФТИ",
        "stage": "in_learning",
        "kam": "kam1@demo",
        "amount": "1200000.00",
        "product": "dpo-ds",
        "program": "Практикум по ML, весна-2027",
        "contract": "Д-2026/041",
        "age": 90,
        "stage_days": 5,
    },
    {
        "title": "МФТИ — корпоративная школа Python для сотрудников",
        "university": "МФТИ",
        "stage": "negotiation",
        "kam": "kam1@demo",
        "amount": "450000.00",
        "product": "corp-edu",
        "program": "Корпоративная школа Python",
        "contract": "Д-2026/041",
        "age": 30,
        "stage_days": 6,
    },
    {
        "title": "Бауманка — ИИ-инженер, осенний поток",
        "university": "Бауманка",
        "stage": "contract_prep",
        "kam": "kam1@demo",
        "amount": "800000.00",
        "product": "dpo-ai",
        "program": "ИИ-инженер, 256 ч.",
        "contract": "Д-2026/052",
        "age": 45,
        "stage_days": 10,
    },
    {
        "title": "СПбПУ — MLOps для инженеров",
        "university": "СПбПУ",
        "stage": "negotiation",
        "kam": "kam2@demo",
        "amount": "600000.00",
        "product": "dpo-ai",
        "program": "MLOps для инженеров",
        "age": 40,
        "stage_days": 20,
    },  # зависшая (порог 14)
    {
        "title": "УрФУ — компьютерное зрение на производстве",
        "university": "УрФУ",
        "stage": "first_contact",
        "kam": "kam2@demo",
        "amount": "350000.00",
        "product": "dpo-ai",
        "program": "Компьютерное зрение на производстве",
        "age": 35,
        "stage_days": 25,
    },  # эскалация (1.5×порога)
    {
        "title": "КФУ — аналитика данных для приёмной комиссии",
        "university": "КФУ",
        "stage": "new",
        "kam": "kam1@demo",
        "amount": "280000.00",
        "product": "dpo-ds",
        "age": 22,
        "stage_days": 22,
    },  # зависшая на new (порог этапа 7)
    {
        "title": "НГУ — сетевая программа Data Science",
        "university": "НГУ",
        "stage": "contract_signed",
        "kam": "kam2@demo",
        "amount": "950000.00",
        "product": "dpo-ds",
        "age": 60,
        "stage_days": 3,
    },
    {
        "title": "ДВФУ — пилот корпоративного обучения",
        "university": "ДВФУ",
        "stage": "first_contact",
        "kam": "kam1@demo",
        "amount": "150000.00",
        "product": "corp-edu",
        "age": 12,
        "stage_days": 4,
    },
    {
        "title": "ЮФУ — ДПО Искусственный интеллект",
        "university": "ЮФУ",
        "stage": "rejected",
        "kam": "kam2@demo",
        "amount": "500000.00",
        "product": "dpo-ai",
        "age": 50,
        "stage_days": 15,
        "reject_comment": "Вуз выбрал другого поставщика по итогам конкурса",
    },
    {
        "title": "МФТИ — олимпиадная школа (расширение договора)",
        "university": "МФТИ",
        "stage": "completed",
        "kam": "kam1@demo",
        "amount": "300000.00",
        "product": "corp-edu",
        "contract": "Д-2026/041",
        "age": 120,
        "stage_days": 12,  # закрыта 12 дней назад — «Завершено за месяц» не пустое
    },
    {
        "title": "СПбПУ — второй поток MLOps",
        "university": "СПбПУ",
        "stage": "new",
        "kam": "kam2@demo",
        "amount": "620000.00",
        "product": "dpo-ai",
        "program": "MLOps для инженеров",
        "age": 3,
        "stage_days": 3,
    },
    {
        "title": "Бауманка — стажировки студентов в индустрии",
        "university": "Бауманка",
        "stage": "negotiation",
        "kam": "kam1@demo",
        "amount": "200000.00",
        "product": "corp-edu",
        "age": 18,
        "stage_days": 8,
    },
]

DEALS_B2C: list[dict[str, Any]] = [
    {
        "title": "Смирнова О.В. — Практикум по ML",
        "person": "smirnova@example.com",
        "interaction": "dpo_course",
        "stage": "payment_pending",
        "kam": "kam2@demo",
        "amount": "120000.00",
        "program": "Практикум по ML, весна-2027",
        "product": "dpo-ds",
        "age": 14,
        "stage_days": 4,
    },
    {
        "title": "Ковалёв А.Д. — ИИ-инженер",
        "person": "kovalev@example.com",
        "interaction": "dpo_course",
        "stage": "consultation",
        "kam": "kam2@demo",
        "amount": "135000.00",
        "program": "ИИ-инженер, 256 ч.",
        "product": "dpo-ai",
        "age": 6,
        "stage_days": 2,
    },
    {
        "title": "Никитина Д.П. — Аналитик данных с нуля",
        "person": "nikitina@example.com",
        "interaction": "dpo_course",
        "stage": "in_learning",
        "kam": "kam1@demo",
        "amount": "90000.00",
        "program": "Аналитик данных с нуля",
        "product": "dpo-ds",
        "age": 55,
        "stage_days": 20,
    },
    {
        "title": "Фёдоров М.И. — участие в хакатоне",
        "person": "fedorov@example.com",
        "interaction": "event",
        "stage": "rejected",
        "kam": "kam1@demo",
        "amount": None,
        "age": 25,
        "stage_days": 10,
        "reject_comment": "Не вышел на связь после двух напоминаний",
    },
    {
        "title": "ООО «ТехноСофт» — корпоративная школа Python",
        "legal": "ООО «ТехноСофт»",
        "interaction": "corporate_training",
        "stage": "proposal",
        "kam": "kam2@demo",
        "amount": "540000.00",
        "program": "Корпоративная школа Python",
        "product": "corp-edu",
        "age": 20,
        "stage_days": 16,
    },  # зависшая B2C
    {
        "title": "АО «ПромАвтоматика» — обучение отдела АСУ ТП",
        "legal": "АО «ПромАвтоматика»",
        "interaction": "corporate_training",
        "stage": "new",
        "kam": "kam1@demo",
        "amount": "700000.00",
        "product": "corp-edu",
        "age": 2,
        "stage_days": 2,
    },
    # лиды с сайта (CMS) ещё не распределены: висят на руководителе, как назначает их
    # интеграция, — сигнал «Без ответственного КАМа» на «Аналитике»
    {
        "title": "Белова К.А. — заявка с сайта: Аналитик данных с нуля",
        "person": "belova@example.com",
        "interaction": "dpo_course",
        "stage": "new",
        "kam": "head@demo",
        "source": "cms",
        "amount": None,
        "program": "Аналитик данных с нуля",
        "product": "dpo-ds",
        "age": 1,
        "stage_days": 1,
    },
    {
        "title": "Громов И.С. — заявка с сайта: ИИ-инженер",
        "person": "gromov@example.com",
        "interaction": "dpo_course",
        "stage": "new",
        "kam": "head@demo",
        "source": "cms",
        "amount": None,
        "program": "ИИ-инженер, 256 ч.",
        "product": "dpo-ai",
        "age": 2,
        "stage_days": 2,
    },
]

FEDERAL_PROJECTS = [
    ("Кадры для цифровой экономики", "digital-skills", -200, 500),
    ("Приоритет-2030", "priority-2030", -400, 900),
]

ACTIVITIES: list[dict[str, Any]] = [
    {
        "name": "Осенняя школа Data Science",
        "activity_type": "course",
        "federal_project": "Кадры для цифровой экономики",
        "university": "МФТИ",
        "program": "Практикум по ML, весна-2027",
        "slots": [(7, 3, "online"), (21, 3, "online")],
    },
    {
        "name": "Хакатон «Цифровой прорыв: вузы»",
        "activity_type": "hackathon",
        "federal_project": "Кадры для цифровой экономики",
        "university": None,
        "program": None,
        "slots": [(14, 48, "offline")],
    },
    {
        "name": "Стажировка в индустриальных партнёрах",
        "activity_type": "internship",
        "federal_project": "Приоритет-2030",
        "university": "Бауманка",
        "program": None,
        "slots": [(30, 0, "offline")],
    },
    {
        "name": "День открытых дверей ДПО",
        "activity_type": "event",
        "federal_project": None,
        "university": "СПбПУ",
        "program": "MLOps для инженеров",
        "slots": [(10, 2, "hybrid")],
    },
]

_FIRST = [
    "Алексей",
    "Мария",
    "Иван",
    "Дарья",
    "Сергей",
    "Анна",
    "Павел",
    "Елена",
    "Никита",
    "Ольга",
]
_LAST = [
    "Соловьев",
    "Козлова",
    "Романов",
    "Белова",
    "Тихонов",
    "Орлова",
    "Захаров",
    "Киселева",
    "Громов",
    "Панина",
]
_MID_M = ["Андреевич", "Игоревич", "Владимирович", "Сергеевич", "Дмитриевич"]
_MID_F = ["Андреевна", "Игоревна", "Владимировна", "Сергеевна", "Дмитриевна"]
_FUNNEL_SPREAD = ["candidate"] * 7 + ["studying"] * 6 + ["graduate"] * 4 + ["talent_pool"] * 3
_FUNNEL_PATH = ["candidate", "studying", "graduate", "talent_pool"]


def _students_dataset() -> list[dict[str, Any]]:
    students = []
    for i in range(20):
        female = i % 2
        first = _FIRST[i % 10]
        last = _LAST[i % 10]
        mid = (_MID_F if female else _MID_M)[i % 5]
        full_name = f"{last} {first} {mid}"
        students.append(
            {
                "full_name": full_name,
                "email": f"student{i + 1:02d}@example.com",
                "phone": f"+7 92{i % 10} 10{i:02d}-{i + 10:02d}",
                "funnel_status": _FUNNEL_SPREAD[i],
                "university_idx": i % len(UNIVERSITIES),
                "program_idx": i % len(PROGRAMS),
                "federal_project": FEDERAL_PROJECTS[i % 2][0] if i % 3 else None,
            }
        )
    return students


# --------------------------------------------------------------------------------------
# Секции сида
# --------------------------------------------------------------------------------------


async def seed_users(session: AsyncSession) -> dict[str, AppUser]:
    users: dict[str, AppUser] = {}
    for spec in DEMO_USERS:
        user = (
            await session.execute(
                select(AppUser).where(func.lower(AppUser.username) == spec["username"].lower())
            )
        ).scalar_one_or_none()
        if user is None:
            user = AppUser(
                keycloak_sub=_demo_sub(spec["username"]),
                username=spec["username"],
                email=spec["email"],
                full_name=spec["full_name"],
                roles=spec["roles"],
            )
            session.add(user)
            await session.flush()
        users[spec["username"]] = user
    for spec in DEMO_USERS:
        manager = spec.get("manager")
        if manager and users[spec["username"]].manager_id is None:
            users[spec["username"]].manager_id = users[manager].id
    await session.flush()
    return users


STAGE_THRESHOLD_FALLBACK_MAX = 60  # CHECK из 0001, пока предел не поднят миграцией


async def stage_threshold_limit(session: AsyncSession) -> int:
    """Верхний предел порога этапа по CHECK `workflow_stage` в БД.

    Сид пишет пороги из JSON (у этапа обучения — 120 дней); до миграции, поднимающей
    предел до 365, БД держит 1–60 — тогда порог прижимается к пределу, а не роняет сид.
    """
    rows = (
        await session.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'workflow_stage'::regclass AND contype = 'c'"
            )
        )
    ).scalars()
    for definition in rows:
        if "stuck_threshold_days" not in definition:
            continue
        match = re.search(r"stuck_threshold_days\s*<=\s*(\d+)", definition) or re.search(
            r"BETWEEN\s+\d+\s+AND\s+(\d+)", definition
        )
        if match:
            return int(match.group(1))
    return STAGE_THRESHOLD_FALLBACK_MAX


def _clamped_threshold(stage_spec: dict[str, Any], limit: int) -> int | None:
    value = stage_spec["stuck_threshold_days"]
    if value is None or value <= limit:
        return value
    print(  # noqa: T201
        f"seed: порог этапа «{stage_spec['name']}» {value} дн. выше предела БД ({limit}) — "
        f"записан {limit}; после миграции предела повторите сид"
    )
    return limit


async def _seed_one_workflow(
    session: AsyncSession, spec: dict[str, Any], admin: AppUser
) -> tuple[Workflow, dict[str, WorkflowStage]]:
    threshold_limit = await stage_threshold_limit(session)
    workflow = (
        await session.execute(select(Workflow).where(Workflow.code == spec["code"]))
    ).scalar_one_or_none()
    if workflow is None:
        workflow = Workflow(
            code=spec["code"],
            name=spec["name"],
            description=spec.get("description"),
            stuck_threshold_days=spec["stuck_threshold_days"],
            updated_by=admin.id,
        )
        session.add(workflow)
        await session.flush()

    existing_stages = (
        (
            await session.execute(
                select(WorkflowStage).where(
                    WorkflowStage.workflow_id == workflow.id,
                    WorkflowStage.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    stages: dict[str, WorkflowStage] = {s.code: s for s in existing_stages}
    for stage_spec in spec["stages"]:
        stage = stages.get(stage_spec["code"])
        if stage is None:
            stage = WorkflowStage(workflow_id=workflow.id, code=stage_spec["code"])
            session.add(stage)
            stages[stage_spec["code"]] = stage
        stage.name = stage_spec["name"]
        stage.color = stage_spec["color"]
        stage.sort_order = stage_spec["sort_order"]
        stage.is_initial = stage_spec["is_initial"]
        stage.is_terminal = stage_spec["is_terminal"]
        stage.terminal_outcome = stage_spec["terminal_outcome"]
        stage.stuck_threshold_days = _clamped_threshold(stage_spec, threshold_limit)
        stage.triggers_lms_handover = stage_spec["triggers_lms_handover"]
        stage.ui_position = stage_spec["ui_position"]
    await session.flush()

    existing_transitions = (
        (
            await session.execute(
                select(WorkflowTransition).where(
                    WorkflowTransition.workflow_id == workflow.id,
                    WorkflowTransition.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    by_pair = {(t.from_stage_id, t.to_stage_id): t for t in existing_transitions}
    for tr_spec in spec["transitions"]:
        pair = (stages[tr_spec["from"]].id, stages[tr_spec["to"]].id)
        transition = by_pair.get(pair)
        if transition is None:
            transition = WorkflowTransition(
                workflow_id=workflow.id, from_stage_id=pair[0], to_stage_id=pair[1]
            )
            session.add(transition)
            by_pair[pair] = transition
        transition.name = tr_spec["name"]
        transition.is_return = tr_spec["is_return"]
        transition.requires_comment = tr_spec["requires_comment"]
        transition.allowed_roles = tr_spec["allowed_roles"]
        transition.condition = tr_spec["condition"]
        transition.sort_order = tr_spec["sort_order"]
    await session.flush()
    return workflow, stages


async def seed_workflows(
    session: AsyncSession, admin: AppUser
) -> dict[str, tuple[Workflow, dict[str, WorkflowStage]]]:
    result = {}
    for code in ("b2b", "b2c"):
        spec = json.loads((SEEDS_DIR / f"{code}.json").read_text(encoding="utf-8"))
        result[code] = await _seed_one_workflow(session, spec, admin)
    return result


# демо-роли F2 (Р-F2-11): права КАМа, но только в одном процессе; назначаются в Альме
# (realm Keycloak не трогаем), на демо kam2@demo получает «КАМ B2C»
DEMO_ROLES: list[dict[str, Any]] = [
    {
        "code": "kam_b2b",
        "name": "КАМ B2B",
        "description": "Права КАМа только в процессе B2B (вузы)",
        "workflow_type": "b2b",
    },
    {
        "code": "kam_b2c",
        "name": "КАМ B2C",
        "description": "Права КАМа только в процессе B2C (физлица и юрлица)",
        "workflow_type": "b2c",
    },
]


async def _set_role_permissions(
    session: AsyncSession, role: AppRole, permissions: frozenset[str] | set[str]
) -> None:
    """Набор прав роли ровно `permissions`: лишние удаляются, недостающие добавляются."""
    current = set(
        (
            await session.execute(
                select(AppRolePermission.permission_code).where(
                    AppRolePermission.role_id == role.id
                )
            )
        ).scalars()
    )
    extra = current - set(permissions)
    if extra:
        await session.execute(
            delete(AppRolePermission).where(
                AppRolePermission.role_id == role.id,
                AppRolePermission.permission_code.in_(sorted(extra)),
            )
        )
    session.add_all(
        AppRolePermission(role_id=role.id, permission_code=code)
        for code in sorted(set(permissions) - current)
    )


async def seed_demo_roles(
    session: AsyncSession,
    workflows: dict[str, tuple[Workflow, dict[str, WorkflowStage]]],
    *,
    commit: bool = True,
) -> dict[str, AppRole]:
    """Каталог прав (сверка с кодом), демо-роли «КАМ B2B»/«КАМ B2C» и их коды в переходах.

    Идемпотентно: роль создаётся или приводится к правам системной роли kam (ровно к ним:
    права, добавленные на репетиции, снимаются) и своему процессу; код роли добавляется
    в `allowed_roles` переходов своего процесса, где есть `kam`. Commit — здесь же, затем
    сброс кэша схем `wf:{id}` и каталога ролей; сброс стенда (`commit=False`) фиксирует
    всё одной транзакцией в конце.
    """
    from app.core.rbac_catalog import SYSTEM_ROLES
    from app.modules.access.bootstrap import seed_rbac_catalog
    from app.modules.workflow.service import invalidate_workflow_cache

    await seed_rbac_catalog(session)
    kam_permissions = SYSTEM_ROLES["kam"].permissions
    roles: dict[str, AppRole] = {}
    for spec in DEMO_ROLES:
        role = (
            await session.execute(select(AppRole).where(AppRole.code == spec["code"]))
        ).scalar_one_or_none()
        if role is None:
            role = AppRole(code=spec["code"], is_system=False, is_locked=False)
            session.add(role)
        role.name = spec["name"]
        role.description = spec["description"]
        role.workflow_types = [spec["workflow_type"]]
        role.record_scope = "all"
        await session.flush()
        await _set_role_permissions(session, role, kam_permissions)
        roles[spec["code"]] = role

        workflow, _stages = workflows[spec["workflow_type"]]
        transitions = (
            (
                await session.execute(
                    select(WorkflowTransition).where(
                        WorkflowTransition.workflow_id == workflow.id,
                        WorkflowTransition.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        for transition in transitions:
            allowed = list(transition.allowed_roles or [])
            if "kam" in allowed and spec["code"] not in allowed:
                transition.allowed_roles = [*allowed, spec["code"]]
    await session.flush()
    if not commit:
        return roles
    await session.commit()
    for workflow, _stages in workflows.values():
        await invalidate_workflow_cache(workflow.id)
    reset_role_catalog()
    return roles


async def seed_interaction_types(session: AsyncSession, admin: AppUser) -> dict[str, uuid.UUID]:
    result: dict[str, uuid.UUID] = {}
    for code, name, description, sort_order in INTERACTION_TYPES:
        row = (
            await session.execute(
                select(InteractionType).where(
                    InteractionType.pipeline == "b2c",
                    func.lower(InteractionType.code) == code,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = InteractionType(
                pipeline="b2c",
                code=code,
                name=name,
                description=description,
                sort_order=sort_order,
                created_by=admin.id,
            )
            session.add(row)
            await session.flush()
        result[code] = row.id
    return result


async def seed_app_settings(session: AsyncSession, *, force: bool = False) -> None:
    """Настройки по умолчанию; `force` (сброс стенда) возвращает значения сида и
    тем, что изменили на репетиции (фичефлаги, пороги)."""
    for key, value, description in APP_SETTINGS:
        row = await session.get(AppSetting, key)
        if row is None:
            session.add(AppSetting(key=key, value=value, description=description))
        elif force:
            row.value = value
            row.description = description
    await session.flush()


# Состояние онбординга (frontend/src/features/onboarding/types.ts, ONBOARDING_VERSION):
# пресет пользователя screen="onboarding", name="state" в app_setting['ui:presets:{id}']
ONBOARDING_SCREEN = "onboarding"
ONBOARDING_PRESET = "state"
ONBOARDING_VERSION = 1
# учётки, под которыми показывают стенд: приветствие пройдено, чек-лист свёрнут
ONBOARDING_HIDDEN_FOR = tuple(spec["username"] for spec in DEMO_USERS)


def _hidden_onboarding_state(previous: dict[str, Any] | None) -> dict[str, Any]:
    state = dict(previous or {})
    state.setdefault("tours", {})
    state.setdefault("checklist", {})
    state.update(welcome_seen=True, checklist_hidden=True, version=ONBOARDING_VERSION)
    return state


async def hide_demo_onboarding(session: AsyncSession, users: dict[str, AppUser]) -> int:
    """Приветствие и чек-лист «Начало работы» не всплывают на демо-учётках (идемпотентно;
    пройденные туры и отмеченные пункты сохраняются)."""
    from app.modules.ui.presets import add_preset, presets_key

    hidden = 0
    for username in ONBOARDING_HIDDEN_FOR:
        user = users.get(username)
        if user is None:
            continue
        key = presets_key(user.id)
        row = await session.get(AppSetting, key)
        stored = row.value.get("items") if row and isinstance(row.value, dict) else None
        items = [dict(item) for item in stored or [] if isinstance(item, dict)]
        preset = next(
            (
                item
                for item in items
                if item.get("screen") == ONBOARDING_SCREEN and item.get("name") == ONBOARDING_PRESET
            ),
            None,
        )
        if preset is None:
            add_preset(
                items,
                {
                    "screen": ONBOARDING_SCREEN,
                    "name": ONBOARDING_PRESET,
                    "state": _hidden_onboarding_state(None),
                },
            )
        else:
            preset["state"] = _hidden_onboarding_state(preset.get("state"))
        if row is None:
            session.add(
                AppSetting(key=key, value={"items": items}, description="Пресеты UI пользователя")
            )
        else:
            row.value = {"items": items}
        hidden += 1
    await session.flush()
    return hidden


async def seed_notification_rules(session: AsyncSession, admin: AppUser) -> None:
    for spec in NOTIFICATION_RULES:
        stmt = select(NotificationRule).where(
            NotificationRule.event_type == spec["event_type"],
            NotificationRule.recipient_type == spec["recipient_type"],
            NotificationRule.channel == spec["channel"],
        )
        recipient_value = spec.get("recipient_value")
        stmt = (
            stmt.where(NotificationRule.recipient_value == recipient_value)
            if recipient_value
            else stmt.where(NotificationRule.recipient_value.is_(None))
        )
        if (await session.execute(stmt)).scalar_one_or_none() is None:
            session.add(
                NotificationRule(
                    event_type=spec["event_type"],
                    recipient_type=spec["recipient_type"],
                    recipient_value=recipient_value,
                    channel=spec["channel"],
                    params=spec.get("params", {}),
                    created_by=admin.id,
                )
            )
    await session.flush()


async def seed_universities(
    session: AsyncSession, users: dict[str, AppUser]
) -> dict[str, University]:
    result: dict[str, University] = {}
    for spec in UNIVERSITIES:
        row = (
            await session.execute(
                select(University).where(func.lower(University.name) == spec["name"].lower())
            )
        ).scalar_one_or_none()
        if row is None:
            row = University(
                name=spec["name"],
                short_name=spec["short_name"],
                inn=spec["inn"],
                region=spec["region"],
                city=spec["city"],
                website=spec["website"],
                kam_user_id=users[spec["kam"]].id,
            )
            session.add(row)
            await session.flush()
        result[spec["short_name"]] = row

    for contact in UNIVERSITY_CONTACTS:
        university = result[contact["university"]]
        exists = (
            await session.execute(
                select(UniversityContact.id).where(
                    UniversityContact.university_id == university.id,
                    UniversityContact.full_name == contact["full_name"],
                )
            )
        ).first()
        if exists is None:
            session.add(
                UniversityContact(
                    university_id=university.id,
                    full_name=contact["full_name"],
                    position=contact["position"],
                    email=contact["email"],
                    phone=contact["phone"],
                    is_primary=True,
                )
            )
    await session.flush()
    return result


async def seed_catalog(
    session: AsyncSession, universities: dict[str, University], users: dict[str, AppUser]
) -> tuple[dict[str, Product], dict[str, Program]]:
    products: dict[str, Product] = {}
    for code, name, product_type, description in PRODUCTS:
        row = (
            await session.execute(select(Product).where(func.lower(Product.code) == code))
        ).scalar_one_or_none()
        if row is None:
            row = Product(code=code, name=name, product_type=product_type, description=description)
            session.add(row)
            await session.flush()
        products[code] = row

    programs: dict[str, Program] = {}
    admin = users["admin@demo"]
    for spec in PROGRAMS:
        row = (
            await session.execute(select(Program).where(Program.name == spec["name"]))
        ).scalar_one_or_none()
        if row is None:
            row = Program(
                name=spec["name"],
                product_id=products[spec["product"]].id,
                university_id=(universities[spec["university"]].id if spec["university"] else None),
                priority_rank=spec["priority_rank"],
                duration_hours=spec["duration_hours"],
                seats=spec["seats"],
                published_to_cms=spec["published_to_cms"],
                priority_updated_by=admin.id,
                priority_updated_at=_NOW,
            )
            session.add(row)
            await session.flush()
        programs[spec["name"]] = row
    return products, programs


async def seed_it_directions(
    session: AsyncSession, products: dict[str, Product], programs: dict[str, Program]
) -> dict[str, ItDirection]:
    """Справочник ИТ-направлений и привязка демо-каталога. Повтор ничего не дублирует и
    не перетирает ручную правку: направление ставится программе без направления, связи —
    продукту, у которого их ещё нет."""
    from app.modules.crm.directions import link_product_directions

    directions: dict[str, ItDirection] = {}
    for index, (code, name, description) in enumerate(IT_DIRECTIONS):
        row = (
            await session.execute(select(ItDirection).where(func.lower(ItDirection.code) == code))
        ).scalar_one_or_none()
        if row is None:
            row = ItDirection(
                code=code, name=name, description=description, sort_order=(index + 1) * 10
            )
            session.add(row)
            await session.flush()
        directions[code] = row

    for program_name, code in PROGRAM_DIRECTIONS.items():
        program = programs.get(program_name)
        if program is not None and program.direction_id is None:
            program.direction_id = directions[code].id
    for product_code, codes in PRODUCT_DIRECTIONS.items():
        product = products.get(product_code)
        if product is None:
            continue
        has_links = (
            await session.execute(
                select(func.count())
                .select_from(ProductDirection)
                .where(ProductDirection.product_id == product.id)
            )
        ).scalar_one()
        if not has_links:
            await link_product_directions(
                session, product.id, [directions[code].id for code in codes]
            )
    await session.flush()
    return directions


async def seed_contracts(
    session: AsyncSession,
    universities: dict[str, University],
    products: dict[str, Product],
    users: dict[str, AppUser],
) -> dict[str, Contract]:
    result: dict[str, Contract] = {}
    for spec in CONTRACTS:
        row = (
            await session.execute(
                select(Contract).where(func.lower(Contract.number) == spec["number"].lower())
            )
        ).scalar_one_or_none()
        if row is None:
            signed = spec["signed"]
            row = Contract(
                number=spec["number"],
                university_id=universities[spec["university"]].id,
                status=spec["status"],
                signed_at=(_NOW - timedelta(days=signed)).date() if signed else None,
                valid_from=(_NOW - timedelta(days=signed)).date() if signed else None,
                valid_to=(
                    (_NOW - timedelta(days=signed) + timedelta(days=spec["valid_days"])).date()
                    if signed and spec["valid_days"]
                    else None
                ),
                amount=Decimal(spec["amount"]),
                responsible_user_id=users[spec["responsible"]].id,
            )
            session.add(row)
            await session.flush()
            for code in spec["products"]:
                session.add(
                    ContractProduct(
                        contract_id=row.id,
                        product_id=products[code].id,
                        added_by=users["admin@demo"].id,
                    )
                )
            await session.flush()
        await _seed_licenses(session, row, spec.get("licenses") or {}, products)
        result[spec["number"]] = row
    return result


async def _seed_licenses(
    session: AsyncSession,
    contract: Contract,
    licenses: dict[str, tuple[int, str]],
    products: dict[str, Product],
) -> None:
    """Поля лицензии в строках договора: заполняются, только пока пусты (повтор сида и
    стенд, засеянный до лицензий, — без сдвига уже записанных дат)."""
    for code, (signed_days, transfer_status) in licenses.items():
        line = await session.get(ContractProduct, (contract.id, products[code].id))
        if line is None or line.license_signed_at or line.license_valid_to:
            continue
        signed = (_NOW - timedelta(days=signed_days)).date()
        line.license_signed_at = signed
        line.license_term_years = 1
        line.license_valid_to = license_end(signed, 1)
        line.transfer_status = transfer_status
        line.transfer_status_updated_at = _NOW - timedelta(days=min(signed_days, 30))
    await session.flush()


async def seed_counterparties(session: AsyncSession) -> dict[str, Counterparty]:
    crypto = get_pii_crypto()
    result: dict[str, Counterparty] = {}
    for person in PERSONS:
        email_hmac = crypto.email_hmac(person["email"])
        row = (
            await session.execute(select(Counterparty).where(Counterparty.email_hmac == email_hmac))
        ).scalar_one_or_none()
        if row is None:
            row = Counterparty(
                kind="person",
                display_name=display_name_from_full_name(person["full_name"]),
                full_name=person["full_name"],
                email=person["email"],
                phone=person["phone"],
                email_hmac=email_hmac,
                full_name_hmac=crypto.full_name_hmac(person["full_name"]),
            )
            session.add(row)
            await session.flush()
        result[person["email"]] = row
    for legal in LEGAL_ENTITIES:
        row = (
            await session.execute(
                select(Counterparty).where(
                    Counterparty.kind == "legal_entity",
                    Counterparty.org_name == legal["org_name"],
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = Counterparty(
                kind="legal_entity",
                display_name=legal["org_name"],
                org_name=legal["org_name"],
                inn=legal["inn"],
                org_email=legal["org_email"],
            )
            session.add(row)
            await session.flush()
        result[legal["org_name"]] = row
    return result


def _stage_path(stages: dict[str, WorkflowStage], target_code: str) -> list[WorkflowStage]:
    """Путь заявки от initial до целевого этапа для правдоподобной истории."""
    ordered = sorted((s for s in stages.values() if not s.is_terminal), key=lambda s: s.sort_order)
    target = stages[target_code]
    if target.is_terminal and target.terminal_outcome == "lost":
        # отказ: пара шагов по основной линии, затем переход в rejected
        return ordered[:2] + [target]
    if target.is_terminal:
        return ordered + [target]
    return [s for s in ordered if s.sort_order <= target.sort_order]


async def _create_deal_with_history(
    session: AsyncSession,
    *,
    workflow: Workflow,
    stages: dict[str, WorkflowStage],
    spec: dict[str, Any],
    responsible: AppUser,
    fields: dict[str, Any],
) -> None:
    exists = (await session.execute(select(Deal.id).where(Deal.title == spec["title"]))).first()
    if exists is not None:
        return

    target = stages[spec["stage"]]
    created_at = _NOW - timedelta(days=spec["age"])
    stage_entered_at = _NOW - timedelta(days=spec["stage_days"])
    status = "open"
    if target.is_terminal and target.terminal_outcome:
        status = target.terminal_outcome

    deal = Deal(
        pipeline=workflow.code,
        workflow_id=workflow.id,
        stage_id=target.id,
        title=spec["title"],
        responsible_user_id=responsible.id,
        amount=Decimal(spec["amount"]) if spec.get("amount") else None,
        status=status,
        stage_entered_at=stage_entered_at,
        last_activity_at=stage_entered_at,
        created_by=responsible.id,
        created_at=created_at,
        updated_at=stage_entered_at,
        **fields,
    )
    session.add(deal)
    await session.flush()

    path = _stage_path(stages, spec["stage"])
    transitions = (
        (
            await session.execute(
                select(WorkflowTransition).where(
                    WorkflowTransition.workflow_id == workflow.id,
                    WorkflowTransition.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    by_pair = {(t.from_stage_id, t.to_stage_id): t for t in transitions}

    steps = max(len(path) - 1, 1)
    step_delta = (spec["age"] - spec["stage_days"]) / steps if steps else 0
    session.add(
        DealStageHistory(
            deal_id=deal.id,
            from_stage_id=None,
            to_stage_id=path[0].id,
            from_stage_name=None,
            to_stage_name=path[0].name,
            actor_user_id=responsible.id,
            kind="manual",
            reason="created",
            created_at=created_at,
        )
    )
    for index in range(1, len(path)):
        prev, current = path[index - 1], path[index]
        transition = by_pair.get((prev.id, current.id))
        is_last = index == len(path) - 1
        session.add(
            DealStageHistory(
                deal_id=deal.id,
                from_stage_id=prev.id,
                to_stage_id=current.id,
                from_stage_name=prev.name,
                to_stage_name=current.name,
                transition_id=transition.id if transition else None,
                is_return=bool(transition.is_return) if transition else False,
                actor_user_id=responsible.id,
                kind="manual",
                comment=spec.get("reject_comment") if is_last and status == "lost" else None,
                created_at=(
                    stage_entered_at if is_last else created_at + timedelta(days=step_delta * index)
                ),
            )
        )
    await session.flush()


async def seed_deals(
    session: AsyncSession,
    workflows: dict[str, tuple[Workflow, dict[str, WorkflowStage]]],
    universities: dict[str, University],
    products: dict[str, Product],
    programs: dict[str, Program],
    contracts: dict[str, Contract],
    counterparties: dict[str, Counterparty],
    interaction_types: dict[str, uuid.UUID],
    users: dict[str, AppUser],
) -> None:
    b2b_workflow, b2b_stages = workflows["b2b"]
    for spec in DEALS_B2B:
        await _create_deal_with_history(
            session,
            workflow=b2b_workflow,
            stages=b2b_stages,
            spec=spec,
            responsible=users[spec["kam"]],
            fields={
                "university_id": universities[spec["university"]].id,
                "product_id": products[spec["product"]].id if spec.get("product") else None,
                "program_id": programs[spec["program"]].id if spec.get("program") else None,
                "contract_id": contracts[spec["contract"]].id if spec.get("contract") else None,
            },
        )

    b2c_workflow, b2c_stages = workflows["b2c"]
    for spec in DEALS_B2C:
        counterparty = (
            counterparties[spec["person"]] if spec.get("person") else counterparties[spec["legal"]]
        )
        await _create_deal_with_history(
            session,
            workflow=b2c_workflow,
            stages=b2c_stages,
            spec=spec,
            responsible=users[spec["kam"]],
            fields={
                "counterparty_id": counterparty.id,
                "interaction_type_id": interaction_types[spec["interaction"]],
                "product_id": products[spec["product"]].id if spec.get("product") else None,
                "program_id": programs[spec["program"]].id if spec.get("program") else None,
                "source": spec.get("source", "manual"),
            },
        )


async def seed_talent_pool(
    session: AsyncSession,
    universities: dict[str, University],
    programs: dict[str, Program],
    counterparties: dict[str, Counterparty],
    users: dict[str, AppUser],
) -> None:
    crypto = get_pii_crypto()
    admin = users["admin@demo"]

    fed_projects: dict[str, FederalProject] = {}
    for name, code, start_shift, duration in FEDERAL_PROJECTS:
        row = (
            await session.execute(
                select(FederalProject).where(func.lower(FederalProject.name) == name.lower())
            )
        ).scalar_one_or_none()
        if row is None:
            row = FederalProject(
                name=name,
                code=code,
                starts_on=(_NOW + timedelta(days=start_shift)).date(),
                ends_on=(_NOW + timedelta(days=start_shift + duration)).date(),
            )
            session.add(row)
            await session.flush()
        fed_projects[name] = row

    university_rows = list(universities.values())
    program_rows = [programs[s["name"]] for s in PROGRAMS]
    students: list[Student] = []
    for index, spec in enumerate(_students_dataset()):
        email_hmac = crypto.email_hmac(spec["email"])
        student = (
            await session.execute(select(Student).where(Student.email_hmac == email_hmac))
        ).scalar_one_or_none()
        if student is None:
            program = program_rows[spec["program_idx"]]
            counterparty = (
                list(counterparties.values())[index % len(PERSONS)] if index < 4 else None
            )
            student = Student(
                full_name=spec["full_name"],
                email=spec["email"],
                phone=spec["phone"],
                email_hmac=email_hmac,
                full_name_hmac=crypto.full_name_hmac(spec["full_name"]),
                display_name=display_name_from_full_name(spec["full_name"]),
                university_id=university_rows[spec["university_idx"]].id,
                product_id=program.product_id,
                program_id=program.id,
                counterparty_id=counterparty.id if counterparty else None,
                federal_project_id=(
                    fed_projects[spec["federal_project"]].id if spec["federal_project"] else None
                ),
                funnel_status=spec["funnel_status"],
                status_changed_at=_NOW - timedelta(days=10 + index),
                created_by=admin.id,
            )
            session.add(student)
            await session.flush()
            path = _FUNNEL_PATH[: _FUNNEL_PATH.index(spec["funnel_status"]) + 1]
            for step, to_status in enumerate(path):
                session.add(
                    StudentStatusHistory(
                        student_id=student.id,
                        from_status=path[step - 1] if step else None,
                        to_status=to_status,
                        actor_user_id=admin.id,
                        created_at=_NOW - timedelta(days=60 - step * 15),
                    )
                )
        students.append(student)

    activities: list[Activity] = []
    for spec in ACTIVITIES:
        activity = (
            await session.execute(select(Activity).where(Activity.name == spec["name"]))
        ).scalar_one_or_none()
        if activity is None:
            activity = Activity(
                name=spec["name"],
                activity_type=spec["activity_type"],
                federal_project_id=(
                    fed_projects[spec["federal_project"]].id if spec["federal_project"] else None
                ),
                university_id=(universities[spec["university"]].id if spec["university"] else None),
                program_id=programs[spec["program"]].id if spec["program"] else None,
                responsible_user_id=users["head@demo"].id,
            )
            session.add(activity)
            await session.flush()
            for days_ahead, duration_hours, fmt in spec["slots"]:
                starts_at = _NOW + timedelta(days=days_ahead)
                session.add(
                    ActivitySchedule(
                        activity_id=activity.id,
                        starts_at=starts_at,
                        ends_at=(
                            starts_at + timedelta(hours=duration_hours) if duration_hours else None
                        ),
                        format=fmt,
                        location="Онлайн" if fmt == "online" else "Кампус вуза",
                    )
                )
        activities.append(activity)

    for index, student in enumerate(students[:12]):
        activity = activities[index % len(activities)]
        exists = (
            await session.execute(
                select(StudentActivity).where(
                    StudentActivity.student_id == student.id,
                    StudentActivity.activity_id == activity.id,
                )
            )
        ).first()
        if exists is None:
            session.add(
                StudentActivity(
                    student_id=student.id,
                    activity_id=activity.id,
                    status="in_progress" if student.funnel_status == "studying" else "registered",
                )
            )
    await session.flush()


# --------------------------------------------------------------------------------------
# Сброс демо-стенда (make demo-reset)
# --------------------------------------------------------------------------------------

# Таблицы, которые сброс не очищает. app_user — keycloak_sub, привязки Telegram,
# руководители; каталог ролей и прав приводится к исходному отдельно; app_setting —
# пресеты пользователей (ключи APP_SETTINGS перезаписываются); workflow — id процессов
# стабильны (этапы и переходы пересоздаются из JSON); audit_log — журнал не стирается.
RESET_KEEP_TABLES = frozenset(
    {
        "alembic_version",
        "app_user",
        "app_permission",
        "app_role",
        "app_role_permission",
        "app_setting",
        "workflow",
        "audit_log",
    }
)
# кэши KeyDB, которые после сброса указывают на удалённые строки: отчёты, схемы
# процессов, статусы выгрузок, «уже уведомляли о зависании»; коды привязки Telegram
# (`tg:link:*`) и дедуп входящих событий интеграций не трогаем
RESET_CACHE_PATTERNS = ("dash:*", "wf:*", "export:*", "stuck:guard:*")
RESET_LOCK_TIMEOUT = "30s"


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


async def demo_reset_tables(session: AsyncSession) -> list[str]:
    """Доменные таблицы схемы public, которые очищает сброс (всё, кроме RESET_KEEP_TABLES)."""
    names = (
        await session.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
        )
    ).scalars()
    return [name for name in names if name not in RESET_KEEP_TABLES]


async def truncate_demo_data(session: AsyncSession) -> tuple[list[str], list[tuple[str, str]]]:
    """Очищает доменные таблицы в текущей транзакции; возвращает их и файлы MinIO.

    TRUNCATE без CASCADE: если таблица из RESET_KEEP_TABLES ссылается на очищаемую,
    PostgreSQL откажет целиком — app_user и журнал аудита каскадом не очистятся никогда.
    Файлы стираются из MinIO только после commit (`remove_stale_files`).
    """
    from app.models import FileObject

    await session.execute(text(f"SET LOCAL lock_timeout = '{RESET_LOCK_TIMEOUT}'"))
    files = [
        (bucket, key)
        for bucket, key in (
            await session.execute(select(FileObject.s3_bucket, FileObject.s3_key))
        ).all()
    ]
    tables = await demo_reset_tables(session)
    if tables:
        await session.execute(text(f"TRUNCATE TABLE {', '.join(map(_quote_ident, tables))}"))
    return tables, files


async def reset_workflow_rows(session: AsyncSession) -> None:
    """Название, описание и порог процессов — к JSON сида (строки и их id сохраняются)."""
    for code in ("b2b", "b2c"):
        spec = json.loads((SEEDS_DIR / f"{code}.json").read_text(encoding="utf-8"))
        workflow = (
            await session.execute(select(Workflow).where(Workflow.code == code))
        ).scalar_one_or_none()
        if workflow is None:
            continue
        workflow.name = spec["name"]
        workflow.description = spec.get("description")
        workflow.stuck_threshold_days = spec["stuck_threshold_days"]
    await session.flush()


async def reset_demo_users(session: AsyncSession) -> int:
    """Демо-учёткам — роли, руководитель и активность сида (kam2 снова «КАМ», а не
    «КАМ B2C» после репетиции). keycloak_sub, ФИО из Keycloak и Telegram не трогаются."""
    from app.tools.seed_activity import HEAD, KAMS

    expected: dict[str, tuple[list[str], str | None, bool]] = {
        spec["username"].lower(): (list(spec["roles"]), spec.get("manager"), True)
        for spec in DEMO_USERS
    }
    for username, _full_name, _persona, active in KAMS:
        expected.setdefault(username.lower(), (["kam"], HEAD, active))
    rows = (
        (
            await session.execute(
                select(AppUser).where(func.lower(AppUser.username).in_(sorted(expected)))
            )
        )
        .scalars()
        .all()
    )
    by_name = {user.username.lower(): user for user in rows}
    for name, (roles, manager, active) in expected.items():
        user = by_name.get(name)
        if user is None:
            continue
        user.roles = roles
        user.is_active = active
        boss = by_name.get(manager.lower()) if manager else None
        user.manager_id = boss.id if boss else None
    await session.flush()
    return len(by_name)


async def reset_access_catalog(session: AsyncSession) -> list[str]:
    """Роли и права F2 — к исходным: системные роли к набору из кода (название, процессы,
    область, права), пользовательские роли, кроме демо-ролей, удаляются и снимаются с
    пользователей. Демо-роли приводит к правам КАМа `seed_demo_roles`."""
    from app.core.rbac_catalog import SYSTEM_ROLES, WORKFLOW_TYPES
    from app.modules.access.bootstrap import seed_rbac_catalog

    await seed_rbac_catalog(session)
    roles = {role.code: role for role in (await session.execute(select(AppRole))).scalars()}
    for code, definition in SYSTEM_ROLES.items():
        role = roles[code]
        role.name = definition.name
        role.description = definition.description
        role.workflow_types = list(WORKFLOW_TYPES)
        role.record_scope = "all"
        await _set_role_permissions(session, role, definition.permissions)
    demo_codes = {spec["code"] for spec in DEMO_ROLES}
    removed = sorted(
        code for code, role in roles.items() if not role.is_system and code not in demo_codes
    )
    if removed:
        removed_ids = [roles[code].id for code in removed]
        await session.execute(
            delete(AppRolePermission).where(AppRolePermission.role_id.in_(removed_ids))
        )
        await session.execute(delete(AppRole).where(AppRole.id.in_(removed_ids)))
        holders = (
            (await session.execute(select(AppUser).where(AppUser.roles.overlap(removed))))
            .scalars()
            .all()
        )
        for user in holders:
            user.roles = [code for code in user.roles if code not in removed]
    await session.flush()
    return removed


async def flush_demo_caches() -> int:
    """Сбрасывает в KeyDB кэши, указывающие на удалённые строки (без KeyDB — пропуск)."""
    from redis.exceptions import RedisError

    from app.core.cache import cache_key, get_cache

    client = get_cache().client
    removed = 0
    try:
        for pattern in RESET_CACHE_PATTERNS:
            keys = [key async for key in client.scan_iter(match=cache_key(pattern), count=500)]
            if keys:
                removed += int(await client.delete(*keys))
    except (RedisError, OSError) as exc:
        print(f"seed: кэш KeyDB не сброшен ({type(exc).__name__}) — истечёт по TTL")  # noqa: T201
    return removed


async def remove_stale_files(files: list[tuple[str, str]]) -> int:
    """Стирает из MinIO объекты очищенных file_object (best effort, после commit)."""
    from app.modules.files.storage import get_storage

    storage = get_storage()
    removed = 0
    for bucket, key in files:
        try:
            ok = await storage.remove(bucket, key)
        except Exception as exc:  # noqa: BLE001 — MinIO недоступен: сброс уже зафиксирован
            print(f"seed: старые файлы не удалены из MinIO ({type(exc).__name__})")  # noqa: T201
            break
        removed += int(ok)
    return removed


async def _after_reset(files: list[tuple[str, str]]) -> dict[str, int]:
    from app.modules.access.service import after_rbac_change

    reset_role_catalog()
    caches = await flush_demo_caches()
    try:
        # реплики и открытые интерфейсы перечитывают каталог ролей сразу, а не по TTL
        await after_rbac_change(role_codes=[spec["code"] for spec in DEMO_ROLES])
    except Exception as exc:  # noqa: BLE001 — без KeyDB каталог обновится по TTL
        print(f"seed: событие access.changed не отправлено ({type(exc).__name__})")  # noqa: T201
    return {"caches": caches, "files_removed": await remove_stale_files(files)}


# --------------------------------------------------------------------------------------
# Точка входа
# --------------------------------------------------------------------------------------


async def run_seed(*, reset: bool = False) -> dict[str, Any]:
    """Сид демо-стенда; `reset=True` — очистка доменных данных и пересев от якоря
    одной транзакцией (при ошибке стенд остаётся в прежнем состоянии)."""
    from app.core.audit import write_audit
    from app.core.config import get_settings

    if reset and get_settings().app_env == "prod":
        raise RuntimeError("Сброс демо-данных запрещён при APP_ENV=prod")
    get_pii_crypto()  # ранний fail с понятной ошибкой, если PII-ключи не заданы
    factory = get_session_factory()
    tables: list[str] = []
    files: list[tuple[str, str]] = []
    removed_roles: list[str] = []
    async with factory() as session:
        if reset:
            tables, files = await truncate_demo_data(session)
            await reset_workflow_rows(session)
        users = await seed_users(session)
        admin = users["admin@demo"]
        workflows = await seed_workflows(session, admin)
        if reset:
            await reset_demo_users(session)
            removed_roles = await reset_access_catalog(session)
        await seed_demo_roles(session, workflows, commit=not reset)
        interaction_types = await seed_interaction_types(session, admin)
        await seed_app_settings(session, force=reset)
        await seed_notification_rules(session, admin)
        universities = await seed_universities(session, users)
        products, programs = await seed_catalog(session, universities, users)
        await seed_it_directions(session, products, programs)
        contracts = await seed_contracts(session, universities, products, users)
        counterparties = await seed_counterparties(session)
        await seed_deals(
            session,
            workflows,
            universities,
            products,
            programs,
            contracts,
            counterparties,
            interaction_types,
            users,
        )
        await seed_talent_pool(session, universities, programs, counterparties, users)
        # история активности КАМов (отчёт F1); модуль импортирует seed — импорт ленивый
        from app.tools.seed_activity import seed_activity_history

        activity = await seed_activity_history(
            session, workflows, universities, products, programs, users, now=demo_now()
        )
        # просрочки: сценарии, передача портфеля, журнал напоминаний, реакции
        from app.tools.seed_sla import seed_sla_history

        overdue = await seed_sla_history(session, now=demo_now())
        await hide_demo_onboarding(session, users)
        if reset:
            await write_audit(
                session,
                actor_user_id=None,
                action="stand.demo_reset",
                entity_type="stand",
                entity_id=None,
                after={
                    "anchor": demo_now().isoformat(),
                    "tables": len(tables),
                    "removed_roles": removed_roles,
                },
            )
        await session.commit()
    if reset:
        for workflow, _stages in workflows.values():
            from app.modules.workflow.service import invalidate_workflow_cache

            await invalidate_workflow_cache(workflow.id)
        after = await _after_reset(files)
        print(  # noqa: T201
            f"seed: сброс — очищено таблиц {len(tables)}, удалено ролей {len(removed_roles)}, "
            f"файлов из MinIO {after['files_removed']}, ключей кэша {after['caches']}"
        )
    print(  # noqa: T201
        "seed: активность КАМов — новых заявок {deals}, договоров {contracts}, файлов {files}, "
        "мероприятий {activities}, студентов {students}".format(**activity)
    )
    print(  # noqa: T201
        "seed: просрочки — сценарных заявок {deals}, передач {transfers}, напоминаний "
        "{reminders}, эскалаций {escalations}, реакций {reactions}".format(**overdue)
    )
    anchor = demo_now().astimezone(MSK).strftime("%d.%m.%Y %H:%M МСК")
    print(f"seed: демо-данные загружены от {anchor} (повторный запуск безопасен)")  # noqa: T201
    return activity


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.tools.seed", description="Сид демо-стенда «Альма»"
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="очистить доменные данные и засеять заново (app_user и аудит сохраняются)",
    )
    parser.add_argument(
        "--anchor",
        default=None,
        help=f"«сейчас» демо-данных: ГГГГ-ММ-ДД (09:00 МСК) или ISO; иначе {DEMO_ANCHOR_ENV}",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.anchor:
        set_demo_now(resolve_demo_anchor(args.anchor))
    if demo_now() > datetime.now(timezone.utc) + timedelta(hours=1):
        print(  # noqa: T201
            "seed: якорь в будущем — до него часть событий будет «из будущего»; "
            "так готовят стенд к дню показа"
        )
    asyncio.run(_amain(reset=args.reset))
    return 0


async def _amain(*, reset: bool = False) -> None:
    from app.core.cache import close_cache

    try:
        await run_seed(reset=reset)
    finally:
        await dispose_engine()
        await close_cache()


if __name__ == "__main__":
    sys.exit(main())
