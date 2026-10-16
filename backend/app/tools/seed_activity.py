"""Демо-история для отчёта «Активность КАМов» (`python -m app.tools.seed_activity`).

Базовый сид (`app.tools.seed`) даёт 2 КАМов и 18 заявок без комментариев и файлов —
для отчёта «кто, когда, с какими вузами, где остановились» этого мало. Модуль
дополняет стенд правдоподобной активностью ~20 КАМов за сентябрь–октябрь 2026:
- 18 КАМов (kam03…kam20@demo, руководитель — head@demo) с разными профилями:
  активные, средние, вялые, «пропавший» (активность только до периода) и уволившийся
  (неактивен, заявки закрыты летом). Пользователи — зеркала без учётки Keycloak:
  в отчётах они видны, войти под ними нельзя (синхронизация их не деактивирует);
- 48 вузов, закреплённых за КАМами, включая «Университет ИТМО» (kam1) и «Томский
  государственный университет» (kam2) — закреплены, но контакта нет. Названия — реальные
  вузы (среди них партнёры «ИТ Школы», о которых сообщалось публично: МЭИ, МТУСИ,
  Мосполитех, РАНХиГС, Центральный университет); заявки, этапы, договоры и комментарии
  демонстрационные и не утверждают ничего о действительных отношениях с вузом;
- заявки B2B/B2C с историей переходов: ручные переходы КАМа, подписание договора
  руководителем (переход только head_kam/admin), событие ЛМС «обучение завершено»
  (запись kind=integration без смены этапа — как пишет её вебхук ЛМС) и закрытие КАМом,
  возвраты на предыдущий этап; часть заявок зависает дольше порога;
- исторические заявки kam1/kam2 возрастом 130–330 дней — «Прошлый квартал» (Q3)
  отличается от Q2 по всем KPI;
- комментарии и файлы КАМов (плюс пара комментариев руководителя — «чужие»
  действия в отчёт не идут), прошедшие мероприятия с вузами, студенты на программах
  с историей воронки (для блока «пул талантов» сводки);
- договоры вузов: вуз, чья заявка дошла до «Договор подписан», получает действующий
  договор на год с даты подписания (KPI «Вузы с договором» и покрытие вузов не пустые);
  вуз с договором из базового сида второго не получает — «один вуз — один договор».

Все даты — от якоря сида (`app.tools.seed.demo_now()`: момент запуска или
`DEMO_ANCHOR_DATE`), поэтому к показу стенд пересевают (`make demo-reset`), а не
повторяют идемпотентный сид. Закрытые заявки КАМов не закрываются позже чем за 3 дня до
якоря, и минимум несколько закрытий приходится на последние 3–20 дней — KPI «за месяц»
не пустые. Заявок с `source='lms'` нет: ЛМС заявок не создаёт и этапы не двигает — она
только пишет события в историю заявки (`kind=integration`, этап тот же).
Открытые заявки активных КАМов стоят на этапе не дольше своего темпа, а старые закрыты:
зависших — меньшинство (вялые и «пропавший» КАМ, пара заявок средних).

Окно активности — с 01.09.2026 по «сейчас»; если сид запускают вне хакатонного
окна (раньше 15.09 или позже конца декабря 2026) — последние 45 дней, чтобы пресеты
«Неделя/Месяц/Квартал» не были пустыми. План строится детерминированно
(`random.Random` с фиксированным зерном) чистой функцией `build_activity_plan(now)`.
Идемпотентность — по натуральным ключам (username, название вуза/мероприятия,
заголовок заявки, тело комментария, имя файла, HMAC email): повтор ничего не дублирует.
Файлы кладутся в MinIO через `store_bytes`; без MinIO шаг пропускается с сообщением.
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.pii import display_name_from_full_name, get_pii_crypto
from app.models import (
    Activity,
    ActivitySchedule,
    AppUser,
    Contract,
    ContractProduct,
    Counterparty,
    Deal,
    DealComment,
    DealStageHistory,
    FileObject,
    InteractionType,
    Product,
    Program,
    Student,
    StudentStatusHistory,
    University,
    Workflow,
    WorkflowStage,
    WorkflowTransition,
)
from app.tools.seed import SEEDS_DIR, _demo_sub, demo_now

MSK = timezone(timedelta(hours=3), "MSK")
WINDOW_START = datetime(2026, 9, 1, tzinfo=MSK)
FALLBACK_WINDOW_DAYS = 45
PLAN_SEED = 20261016

HEAD = "head@demo"
ADMIN = "admin@demo"

# --------------------------------------------------------------------------------------
# Справочные данные плана
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Persona:
    b2b: int  # сколько заявок B2B сгенерировать
    b2c: float  # ожидаемое число B2C (дробная часть — вероятность ещё одной)
    dwell: tuple[float, float]  # дней на этапе
    comments: tuple[int, int]
    files: tuple[int, int]
    events: int
    recent: float  # доля «свежих» заявок, двигавшихся в окне


PERSONAS: dict[str, Persona] = {
    "high": Persona(6, 1.0, (4, 12), (3, 6), (1, 2), 2, 0.85),
    "medium": Persona(4, 0.5, (6, 18), (1, 4), (0, 1), 1, 0.7),
    "low": Persona(3, 0.0, (14, 32), (0, 2), (0, 1), 0, 0.45),
    "idle": Persona(2, 0.0, (10, 25), (1, 2), (0, 0), 0, 0.0),
    "left": Persona(3, 0.0, (6, 15), (1, 3), (0, 1), 0, 0.0),
}

# (username, ФИО, профиль, активен); kam1/kam2 уже есть в базовом сиде — им только «добор»
KAMS: list[tuple[str, str, str, bool]] = [
    ("kam1@demo", "Петров Константин Андреевич", "high", True),
    ("kam2@demo", "Иванова Мария Сергеевна", "medium", True),
    ("kam03@demo", "Смирнов Алексей Викторович", "high", True),
    ("kam04@demo", "Кузнецова Екатерина Олеговна", "high", True),
    ("kam05@demo", "Волков Дмитрий Сергеевич", "medium", True),
    ("kam06@demo", "Попова Анастасия Игоревна", "medium", True),
    ("kam07@demo", "Соколов Игорь Николаевич", "high", True),
    ("kam08@demo", "Лебедева Ольга Павловна", "medium", True),
    ("kam09@demo", "Козлов Артём Валерьевич", "low", True),
    ("kam10@demo", "Новикова Юлия Александровна", "medium", True),
    ("kam11@demo", "Егорова Светлана Дмитриевна", "medium", True),
    ("kam12@demo", "Павлов Роман Евгеньевич", "low", True),
    ("kam13@demo", "Семёнова Татьяна Викторовна", "high", True),
    ("kam14@demo", "Голубев Максим Андреевич", "medium", True),
    ("kam15@demo", "Виноградова Алина Романовна", "low", True),
    ("kam16@demo", "Богданов Евгений Петрович", "medium", True),
    ("kam17@demo", "Воробьёва Наталья Сергеевна", "medium", True),
    ("kam18@demo", "Фролов Кирилл Олегович", "idle", True),
    ("kam19@demo", "Михайлова Ксения Андреевна", "low", True),
    ("kam20@demo", "Беляев Олег Иванович", "left", False),
]
# сколько заявок «добрать» КАМам базового сида (у них уже 18 заявок на двоих)
EXISTING_KAM_EXTRA = {"kam1@demo": 3, "kam2@demo": 2}
LEFT_AT_DAYS_BEFORE_WINDOW = 3  # уволившийся: последняя активность — конец августа
CLOSE_GAP_DAYS = 3  # закрытия — не позже чем за 3 дня до якоря (последние 3–20 дней — KPI)

# (название, короткое, регион, город, КАМ)
NEW_UNIVERSITIES: list[tuple[str, str, str, str, str]] = [
    ("Университет ИТМО", "ИТМО", "Санкт-Петербург", "Санкт-Петербург", "kam1@demo"),
    ("Томский государственный университет", "ТГУ", "Томская область", "Томск", "kam2@demo"),
    (
        "Национальный исследовательский университет «Высшая школа экономики»",
        "ВШЭ",
        "Москва",
        "Москва",
        "kam03@demo",
    ),
    (
        "Московский государственный университет имени М.В. Ломоносова",
        "МГУ",
        "Москва",
        "Москва",
        "kam03@demo",
    ),
    (
        "Национальный исследовательский ядерный университет «МИФИ»",
        "МИФИ",
        "Москва",
        "Москва",
        "kam03@demo",
    ),
    (
        "Московский технический университет связи и информатики",
        "МТУСИ",
        "Москва",
        "Москва",
        "kam04@demo",
    ),
    ("Московский авиационный институт", "МАИ", "Москва", "Москва", "kam04@demo"),
    ("МИРЭА — Российский технологический университет", "МИРЭА", "Москва", "Москва", "kam04@demo"),
    (
        "Санкт-Петербургский государственный университет",
        "СПбГУ",
        "Санкт-Петербург",
        "Санкт-Петербург",
        "kam05@demo",
    ),
    (
        "Санкт-Петербургский государственный электротехнический университет «ЛЭТИ»",
        "ЛЭТИ",
        "Санкт-Петербург",
        "Санкт-Петербург",
        "kam05@demo",
    ),
    (
        "Балтийский федеральный университет имени И. Канта",
        "БФУ",
        "Калининградская область",
        "Калининград",
        "kam05@demo",
    ),
    ("Томский политехнический университет", "ТПУ", "Томская область", "Томск", "kam06@demo"),
    (
        "Новосибирский государственный технический университет",
        "НГТУ",
        "Новосибирская область",
        "Новосибирск",
        "kam06@demo",
    ),
    ("Сибирский федеральный университет", "СФУ", "Красноярский край", "Красноярск", "kam06@demo"),
    (
        "Самарский национальный исследовательский университет имени С.П. Королёва",
        "Самарский университет",
        "Самарская область",
        "Самара",
        "kam07@demo",
    ),
    (
        "Нижегородский государственный университет имени Н.И. Лобачевского",
        "ННГУ",
        "Нижегородская область",
        "Нижний Новгород",
        "kam07@demo",
    ),
    (
        "Казанский национальный исследовательский технический университет "
        "имени А.Н. Туполева — КАИ",
        "КНИТУ-КАИ",
        "Республика Татарстан",
        "Казань",
        "kam07@demo",
    ),
    ("Университет Иннополис", "Иннополис", "Республика Татарстан", "Иннополис", "kam08@demo"),
    (
        "Пермский национальный исследовательский политехнический университет",
        "ПНИПУ",
        "Пермский край",
        "Пермь",
        "kam08@demo",
    ),
    (
        "Южно-Уральский государственный университет",
        "ЮУрГУ",
        "Челябинская область",
        "Челябинск",
        "kam08@demo",
    ),
    (
        "Уфимский университет науки и технологий",
        "УУНиТ",
        "Республика Башкортостан",
        "Уфа",
        "kam09@demo",
    ),
    ("Тюменский государственный университет", "ТюмГУ", "Тюменская область", "Тюмень", "kam09@demo"),
    (
        "Северо-Кавказский федеральный университет",
        "СКФУ",
        "Ставропольский край",
        "Ставрополь",
        "kam10@demo",
    ),
    (
        "Донской государственный технический университет",
        "ДГТУ",
        "Ростовская область",
        "Ростов-на-Дону",
        "kam10@demo",
    ),
    (
        "Кубанский государственный университет",
        "КубГУ",
        "Краснодарский край",
        "Краснодар",
        "kam10@demo",
    ),
    (
        "Северо-Восточный федеральный университет имени М.К. Аммосова",
        "СВФУ",
        "Республика Саха (Якутия)",
        "Якутск",
        "kam11@demo",
    ),
    (
        "Иркутский национальный исследовательский технический университет",
        "ИРНИТУ",
        "Иркутская область",
        "Иркутск",
        "kam11@demo",
    ),
    ("Алтайский государственный университет", "АлтГУ", "Алтайский край", "Барнаул", "kam11@demo"),
    (
        "Воронежский государственный университет",
        "ВГУ",
        "Воронежская область",
        "Воронеж",
        "kam12@demo",
    ),
    (
        "Белгородский государственный национальный исследовательский университет",
        "НИУ «БелГУ»",
        "Белгородская область",
        "Белгород",
        "kam12@demo",
    ),
    (
        "Национальный исследовательский технологический университет «МИСИС»",
        "МИСИС",
        "Москва",
        "Москва",
        "kam13@demo",
    ),
    ("Национальный исследовательский университет «МЭИ»", "МЭИ", "Москва", "Москва", "kam13@demo"),
    (
        "Российская академия народного хозяйства и государственной службы "
        "при Президенте Российской Федерации",
        "РАНХиГС",
        "Москва",
        "Москва",
        "kam13@demo",
    ),
    ("Московский политехнический университет", "Мосполитех", "Москва", "Москва", "kam14@demo"),
    (
        "Саратовский национальный исследовательский государственный университет "
        "имени Н.Г. Чернышевского",
        "СГУ",
        "Саратовская область",
        "Саратов",
        "kam14@demo",
    ),
    (
        "Волгоградский государственный технический университет",
        "ВолгГТУ",
        "Волгоградская область",
        "Волгоград",
        "kam14@demo",
    ),
    (
        "Северный (Арктический) федеральный университет имени М.В. Ломоносова",
        "САФУ",
        "Архангельская область",
        "Архангельск",
        "kam15@demo",
    ),
    (
        "Петрозаводский государственный университет",
        "ПетрГУ",
        "Республика Карелия",
        "Петрозаводск",
        "kam15@demo",
    ),
    (
        "Ярославский государственный университет имени П.Г. Демидова",
        "ЯрГУ",
        "Ярославская область",
        "Ярославль",
        "kam16@demo",
    ),
    (
        "Владимирский государственный университет",
        "ВлГУ",
        "Владимирская область",
        "Владимир",
        "kam16@demo",
    ),
    ("Центральный университет", "Центральный университет", "Москва", "Москва", "kam16@demo"),
    (
        "Омский государственный технический университет",
        "ОмГТУ",
        "Омская область",
        "Омск",
        "kam17@demo",
    ),
    (
        "Крымский федеральный университет имени В.И. Вернадского",
        "КФУ им. Вернадского",
        "Республика Крым",
        "Симферополь",
        "kam17@demo",
    ),
    (
        "Ижевский государственный технический университет имени М.Т. Калашникова",
        "ИжГТУ",
        "Удмуртская Республика",
        "Ижевск",
        "kam18@demo",
    ),
    (
        "Чувашский государственный университет имени И.Н. Ульянова",
        "ЧГУ",
        "Чувашская Республика",
        "Чебоксары",
        "kam18@demo",
    ),
    (
        "Мордовский государственный университет имени Н.П. Огарёва",
        "МордГУ",
        "Республика Мордовия",
        "Саранск",
        "kam19@demo",
    ),
    ("Курский государственный университет", "КГУ", "Курская область", "Курск", "kam19@demo"),
    (
        "Владивостокский государственный университет",
        "ВВГУ",
        "Приморский край",
        "Владивосток",
        "kam20@demo",
    ),
]
# вузы базового сида (короткое имя → КАМ) — для «добора» kam1/kam2
BASE_UNIVERSITIES: dict[str, str] = {
    "МФТИ": "kam1@demo",
    "Бауманка": "kam1@demo",
    "КФУ": "kam1@demo",
    "ДВФУ": "kam1@demo",
    "СПбПУ": "kam2@demo",
    "УрФУ": "kam2@demo",
    "НГУ": "kam2@demo",
    "ЮФУ": "kam2@demo",
}
# закреплены, но контакта нет — строки «закреплён, нет контакта» и знаменатель охвата
UNTOUCHED_UNIVERSITIES = frozenset({"ИТМО", "ТГУ"})

# (тема, код продукта, программа | None)
TOPICS_B2B: list[tuple[str, str, str | None]] = [
    ("ДПО Data Science для преподавателей", "dpo-ds", "Практикум по ML, весна-2027"),
    ("ИИ-инженер, корпоративный поток", "dpo-ai", "ИИ-инженер, 256 ч."),
    ("аналитик данных с нуля для студентов", "dpo-ds", "Аналитик данных с нуля"),
    ("корпоративная школа Python", "corp-edu", "Корпоративная школа Python"),
    ("MLOps для инженеров вуза", "dpo-ai", "MLOps для инженеров"),
    ("компьютерное зрение на производстве", "dpo-ai", "Компьютерное зрение на производстве"),
    ("стажировки студентов в индустрии", "corp-edu", None),
    ("цифровая кафедра: модуль ИИ", "dpo-ai", None),
    ("летняя школа по анализу данных", "dpo-ds", None),
    ("повышение квалификации ППС", "corp-edu", None),
    ("сетевая программа Data Science", "dpo-ds", None),
    ("хакатон для студентов", "corp-edu", None),
]
TOPICS_B2C: list[tuple[str, str]] = [
    ("Практикум по ML, весна-2027", "dpo-ds"),
    ("ИИ-инженер, 256 ч.", "dpo-ai"),
    ("Аналитик данных с нуля", "dpo-ds"),
    ("MLOps для инженеров", "dpo-ai"),
]

COMMENTS_B2B: list[str] = [
    "Созвонились с проректором по ДПО, ждут коммерческое предложение до пятницы.",
    "Отправил КП и программу курса, жду обратную связь.",
    "Вуз запросил скидку 10% за группу больше 25 человек — согласую с руководителем.",
    "Юристы вуза прислали правки в договор, передал в юротдел.",
    "Встреча перенесена на следующую неделю по просьбе вуза.",
    "Согласовали состав модулей и расписание первого потока.",
    "Уточнили численность группы: 25–30 человек.",
    "Провели презентацию программы для заведующих кафедрами.",
    "Ответа нет вторую неделю — отправил напоминание.",
    "Вуз подтвердил бюджет, ждём служебную записку проректора.",
    "Направил проект договора на согласование.",
    "Обсудили стажировки для лучших студентов потока.",
    "Запросили у вуза список ответственных по ИТ-направлениям.",
    "Договорились о пилотной группе на 10 человек.",
    "Передал контакты преподавателей для синхронизации программы.",
    "Вуз попросил добавить модуль по MLOps.",
    "Согласовали повестку следующей встречи с кафедрой.",
    "Финансовый отдел вуза запросил счёт-оферту.",
    "Уточняем источник финансирования: «Приоритет-2030» или собственные средства.",
    "Согласовали даты старта обучения.",
    "Отправил материалы для сайта вуза.",
    "Повторная встреча с деканом — интерес подтверждён.",
]
COMMENTS_B2C: list[str] = [
    "Клиент уточнил расписание занятий.",
    "Отправил ссылку на оплату.",
    "Клиент просит рассрочку — уточняю условия.",
    "Провёл консультацию по программе.",
    "Клиент сравнивает с курсами конкурентов.",
    "Клиент подтвердил участие во вводном занятии.",
]
COMMENTS_HEAD: list[str] = [
    "Коллеги, по этой заявке нужен статус к планёрке.",
    "Проверьте сроки — заявка висит дольше порога.",
    "Согласовал скидку, можно двигать дальше.",
]
RETURN_REASON = "Вуз вернул договор на доработку условий"
LMS_COMPLETED_EVENT = "lms.learning.completed"
LMS_COMPLETED_LABEL = "LMS: обучение завершено"
LOST_REASONS_B2B = [
    "Вуз выбрал другого поставщика по итогам конкурса",
    "Нет бюджета в этом году",
    "Программа не подошла по срокам",
]
LOST_REASONS_B2C = ["Не вышел на связь после двух напоминаний", "Выбрал другой курс"]
FILE_NAMES = [
    "КП_{short}.pdf",
    "Программа курса.pdf",
    "Проект договора.docx",
    "Протокол встречи.docx",
    "Смета.xlsx",
    "Письмо-запрос вуза.pdf",
]
EVENT_KINDS: list[tuple[str, str]] = [
    ("event", "День карьеры"),
    ("event", "Открытая лекция «ИИ в индустрии»"),
    ("course", "Мастер-класс по анализу данных"),
    ("hackathon", "Хакатон-интенсив"),
    ("event", "Презентация программ ДПО"),
]

# исторические заявки kam1/kam2 (D: «Прошлый квартал» ≠ Q2): (вуз, тема, КАМ, возраст,
# целевой этап, дней на целевом этапе)
HISTORY_DEALS_B2B: list[tuple[str, str, str, int, str, int]] = [
    ("МФТИ", "летняя школа ИИ — 2026", "kam1@demo", 300, "completed", 150),
    ("Бауманка", "повышение квалификации ППС", "kam1@demo", 260, "completed", 110),
    ("КФУ", "цифровая кафедра: модуль ИИ", "kam1@demo", 220, "completed", 95),
    ("ДВФУ", "сетевая программа Data Science", "kam1@demo", 200, "rejected", 120),
    ("МФТИ", "хакатон для студентов", "kam1@demo", 160, "in_learning", 60),
    ("Бауманка", "корпоративный поток Python", "kam1@demo", 140, "negotiation", 45),
    ("СПбПУ", "летняя школа по анализу данных", "kam2@demo", 330, "completed", 200),
    ("УрФУ", "повышение квалификации ППС", "kam2@demo", 250, "completed", 100),
    ("НГУ", "стажировки студентов в индустрии", "kam2@demo", 210, "rejected", 105),
    ("ЮФУ", "цифровая кафедра: модуль ИИ", "kam2@demo", 180, "in_learning", 70),
    ("СПбПУ", "компьютерное зрение для кафедры", "kam2@demo", 150, "contract_prep", 35),
    ("НГУ", "ИИ-инженер, корпоративный поток", "kam2@demo", 130, "in_learning", 40),
]

# комментарии к заявкам базового сида: (заголовок, дней назад, автор | None = КАМ, текст)
BASE_DEAL_COMMENTS: list[tuple[str, int, str | None, str]] = [
    (
        "МФТИ — корпоративная школа Python для сотрудников",
        2,
        None,
        "Согласовали состав модулей, ждём список слушателей от HR вуза.",
    ),
    (
        "МФТИ — корпоративная школа Python для сотрудников",
        9,
        None,
        "Провёл встречу с проректором по ДПО, интерес подтверждён.",
    ),
    (
        "МФТИ — ДПО Data Science, поток весна-2027",
        4,
        None,
        "Слушатели прошли входное тестирование, группа укомплектована.",
    ),
    (
        "МФТИ — ДПО Data Science, поток весна-2027",
        30,
        None,
        "Передал контакты преподавателей для синхронизации программы.",
    ),
    (
        "Бауманка — ИИ-инженер, осенний поток",
        3,
        None,
        "Юристы вуза прислали правки в договор, передал в юротдел.",
    ),
    ("Бауманка — ИИ-инженер, осенний поток", 12, None, "Направил проект договора на согласование."),
    (
        "Бауманка — стажировки студентов в индустрии",
        5,
        None,
        "Обсудили стажировки для лучших студентов потока.",
    ),
    ("КФУ — аналитика данных для приёмной комиссии", 20, None, "Отправил КП, ответа пока нет."),
    (
        "КФУ — аналитика данных для приёмной комиссии",
        8,
        HEAD,
        "Коллеги, по этой заявке нужен статус к планёрке.",
    ),
    (
        "ДВФУ — пилот корпоративного обучения",
        1,
        None,
        "Договорились о пилотной группе на 10 человек.",
    ),
    (
        "СПбПУ — MLOps для инженеров",
        18,
        None,
        "Вуз запросил скидку 10% — согласую с руководителем.",
    ),
    ("СПбПУ — MLOps для инженеров", 6, HEAD, "Проверьте сроки — заявка висит дольше порога."),
    ("СПбПУ — второй поток MLOps", 2, None, "Вуз подтвердил бюджет второго потока."),
    (
        "УрФУ — компьютерное зрение на производстве",
        27,
        None,
        "Ответа нет вторую неделю — отправила напоминание.",
    ),
    ("НГУ — сетевая программа Data Science", 3, None, "Договор подписан, передаём в обучение."),
    ("НГУ — сетевая программа Data Science", 15, None, "Согласовали даты старта обучения."),
    ("Смирнова О.В. — Практикум по ML", 4, None, "Отправила ссылку на оплату."),
    ("Ковалёв А.Д. — ИИ-инженер", 2, None, "Провела консультацию по программе."),
    (
        "ООО «ТехноСофт» — корпоративная школа Python",
        17,
        None,
        "Клиент сравнивает с предложениями конкурентов.",
    ),
]
# файлы к заявкам базового сида: (заголовок, дней назад, имя файла)
BASE_DEAL_FILES: list[tuple[str, int, str]] = [
    ("МФТИ — корпоративная школа Python для сотрудников", 5, "КП_МФТИ.pdf"),
    ("МФТИ — ДПО Data Science, поток весна-2027", 20, "Учебный план.pdf"),
    ("Бауманка — ИИ-инженер, осенний поток", 11, "Проект договора.docx"),
    ("Бауманка — ИИ-инженер, осенний поток", 3, "Протокол разногласий.docx"),
    ("НГУ — сетевая программа Data Science", 4, "Договор (подписанный).pdf"),
    ("СПбПУ — второй поток MLOps", 2, "Смета второго потока.xlsx"),
    ("ДВФУ — пилот корпоративного обучения", 1, "Протокол встречи.docx"),
    ("Смирнова О.В. — Практикум по ML", 4, "Счёт-оферта.pdf"),
]
# прошедшие мероприятия с вузами базового сида: (название, тип, вуз, дней назад)
PAST_ACTIVITIES: list[tuple[str, str, str, int]] = [
    ("День карьеры на Физтехе", "event", "МФТИ", 12),
    ("Открытая лекция «ML в индустрии» в Бауманке", "event", "Бауманка", 40),
    ("Мастер-класс по MLOps в СПбПУ", "course", "СПбПУ", 75),
    ("Хакатон-интенсив УрФУ", "hackathon", "УрФУ", 110),
    ("Презентация программ ДПО в КФУ", "event", "КФУ", 150),
    ("Карьерный день НГУ", "event", "НГУ", 190),
]

_CLIENT_FIRST_M = ["Сергей", "Андрей", "Павел", "Илья", "Михаил", "Денис", "Виктор", "Глеб"]
_CLIENT_FIRST_F = ["Ирина", "Елена", "Юлия", "Анна", "Мария", "Ольга", "Вера", "Полина"]
_CLIENT_LAST = ["Орлов", "Зайцев", "Комаров", "Белов", "Тарасов", "Жуков", "Савельев", "Крылов"]
_CLIENT_MID_M = ["Петрович", "Олегович", "Игоревич", "Андреевич"]
_CLIENT_MID_F = ["Петровна", "Олеговна", "Игоревна", "Андреевна"]
_STUDENT_FIRST_M = ["Михаил", "Тимур", "Артём", "Егор", "Даниил", "Роман", "Лев", "Марк", "Фёдор"]
_STUDENT_FIRST_F = ["Алиса", "Софья", "Варвара", "Ева", "Милана", "Дарина", "Ника", "Таисия"]
_STUDENT_LAST = ["Андреев", "Макаров", "Никитин", "Захаров", "Щербаков", "Поляков", "Ершов"]
_STUDENT_MID_M = ["Алексеевич", "Романович", "Сергеевич", "Ильич"]
_STUDENT_MID_F = ["Алексеевна", "Романовна", "Сергеевна", "Ильинична"]
STUDENT_PROGRAMS = [
    "Практикум по ML, весна-2027",
    "ИИ-инженер, 256 ч.",
    "Аналитик данных с нуля",
    "Компьютерное зрение на производстве",
    "MLOps для инженеров",
]
STUDENTS_COUNT = 36
CLIENTS_COUNT = 16


# --------------------------------------------------------------------------------------
# План (чистая функция)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class StepPlan:
    stage: str
    at: datetime
    actor: str | None  # username; None — система
    kind: str = "manual"
    is_return: bool = False
    comment: str | None = None
    reason: str | None = None  # событие интеграции (у записи ЛМС этап не меняется)


@dataclass
class DealPlan:
    title: str
    pipeline: str
    kam: str
    university: str | None  # короткое имя вуза
    client: str | None  # email клиента B2C
    product: str | None
    program: str | None
    amount: Decimal | None
    steps: list[StepPlan]
    comments: list[tuple[datetime, str, str]] = field(default_factory=list)  # (at, автор, текст)
    files: list[tuple[datetime, str, str]] = field(default_factory=list)  # (at, автор, имя)

    @property
    def created_at(self) -> datetime:
        return self.steps[0].at


@dataclass(frozen=True)
class ActivityPlan:
    name: str
    activity_type: str
    university: str
    kam: str
    slots: tuple[tuple[datetime, int, str], ...]  # (начало, часов, формат)


@dataclass(frozen=True)
class StudentPlan:
    full_name: str
    email: str
    university: str
    program: str
    path: tuple[tuple[str, datetime], ...]


@dataclass(frozen=True)
class ClientPlan:
    full_name: str
    email: str
    phone: str


@dataclass
class ActivityPlanSet:
    window_start: datetime
    window_end: datetime
    kams: list[tuple[str, str, str, bool]]
    universities: list[tuple[str, str, str, str, str]]
    clients: list[ClientPlan]
    deals: list[DealPlan]
    activities: list[ActivityPlan]
    students: list[StudentPlan]


def activity_window(now: datetime) -> tuple[datetime, datetime]:
    """Окно активности: с 01.09.2026 (хакатон) либо последние 45 дней вне этого окна."""
    span = now - WINDOW_START
    if timedelta(days=14) <= span <= timedelta(days=120):
        return WINDOW_START, now
    return now - timedelta(days=FALLBACK_WINDOW_DAYS), now


def _route(pipeline: str, outcome: str, rng: random.Random) -> list[str]:
    """Маршрут по переходам схемы из app/modules/workflow/seeds/*.json."""
    if pipeline == "b2b":
        main = [
            "new",
            "first_contact",
            "negotiation",
            "contract_prep",
            "contract_signed",
            "in_learning",
            "completed",
        ]
        lost_from = ("first_contact", "negotiation", "contract_prep")
    else:
        main = [
            "new",
            "consultation",
            "proposal",
            "payment_pending",
            "paid",
            "in_learning",
            "completed",
        ]
        lost_from = ("new", "consultation")
    if outcome == "won":
        return main
    if outcome == "lost":
        branch = rng.choice(lost_from)
        return main[: main.index(branch) + 1] + ["rejected"]
    # открытая: цель — один из нетерминальных этапов (чаще середина воронки)
    weights = [1, 3, 4, 3, 2, 2] if pipeline == "b2b" else [2, 3, 3, 3, 2, 2]
    target = rng.choices(main[:-1], weights=weights)[0]
    return main[: main.index(target) + 1]


_RETURN_PAIRS = {"b2b": ("contract_prep", "negotiation"), "b2c": ("payment_pending", "proposal")}
_TERMINAL = frozenset({"completed", "rejected"})
_HEAD_ONLY = {("contract_prep", "contract_signed")}  # переход только head_kam/admin


def _simulate(
    rng: random.Random,
    *,
    pipeline: str,
    route: Sequence[str],
    created: datetime,
    dwell: tuple[float, float],
    limit: datetime,
    kam: str,
    lost_reasons: Sequence[str],
    close_limit: datetime | None = None,
) -> list[StepPlan]:
    steps = [StepPlan(route[0], created, kam)]
    moment = created
    returned = False
    for index in range(1, len(route)):
        previous, code = route[index - 1], route[index]
        return_from, return_to = _RETURN_PAIRS[pipeline]
        if previous == return_from and not returned and rng.random() < 0.18:
            # возврат на предыдущий этап и повторное движение вперёд
            gap = rng.uniform(*dwell)
            if moment + timedelta(days=gap * 2) < limit:
                moment += timedelta(days=gap)
                steps.append(
                    StepPlan(return_to, moment, kam, is_return=True, comment=RETURN_REASON)
                )
                moment += timedelta(days=gap * 0.6)
                steps.append(StepPlan(previous, moment, kam))
                returned = True
        gap = rng.uniform(*dwell)
        if moment + timedelta(days=gap) >= limit:
            break  # время вышло — заявка остаётся на текущем этапе (может зависнуть)
        if code in _TERMINAL and close_limit and moment + timedelta(days=gap) > close_limit:
            break  # закрытия не позже чем за CLOSE_GAP_DAYS до якоря — заявка ещё открыта
        actor: str | None = kam
        if (previous, code) in _HEAD_ONLY:
            actor = HEAD
        elif code == "completed" and rng.random() < 0.4:
            # ЛМС сообщает о завершении обучения записью в историю без смены этапа
            # (автоперехода нет — integrations/service.py), заявку закрывает КАМ
            lms_at = moment + timedelta(days=gap * rng.uniform(0.5, 0.9))
            steps.append(
                StepPlan(
                    previous,
                    lms_at,
                    None,
                    kind="integration",
                    comment=LMS_COMPLETED_LABEL,
                    reason=LMS_COMPLETED_EVENT,
                )
            )
        moment += timedelta(days=gap)
        comment = rng.choice(lost_reasons) if code == "rejected" else None
        steps.append(StepPlan(code, moment, actor, comment=comment))
    return steps


def _keep_open_deal_fresh(
    rng: random.Random, steps: list[StepPlan], dwell: tuple[float, float], now: datetime
) -> list[StepPlan]:
    """Открытая заявка КАМа стоит на этапе не дольше его обычного темпа (`dwell`): если
    маршрут кончился давно, вся история сдвигается вперёд. Иначе заявка активного КАМа
    «висела» бы месяцами и зависшими оказались бы почти все открытые заявки стенда."""
    idle = now - steps[-1].at
    keep = timedelta(days=rng.uniform(*dwell))
    if idle <= keep:
        return steps
    shift = idle - keep
    return [replace(step, at=step.at + shift) for step in steps]


def _uniform_time(rng: random.Random, start: datetime, end: datetime) -> datetime | None:
    if end <= start:
        return None
    return start + (end - start) * rng.random()


def _plan_interactions(
    rng: random.Random,
    plan: DealPlan,
    persona: Persona,
    *,
    window: tuple[datetime, datetime],
    hard_limit: datetime,
    bodies: Sequence[str],
    short: str,
) -> None:
    window_start, now = window
    last_step = plan.steps[-1]
    closed = last_step.stage in ("completed", "rejected")
    end = min(hard_limit, last_step.at + timedelta(days=2)) if closed else hard_limit
    end = min(end, now - timedelta(hours=1))
    pool = list(bodies)
    rng.shuffle(pool)
    for _ in range(rng.randint(*persona.comments)):
        if not pool:
            break
        lower = plan.created_at + timedelta(hours=2)
        if persona.recent and rng.random() < 0.8:
            lower = max(lower, window_start)
        at = _uniform_time(rng, lower, end)
        if at is not None:
            plan.comments.append((at, plan.kam, pool.pop()))
    if rng.random() < 0.12:
        at = _uniform_time(rng, max(plan.created_at, window_start), end)
        if at is not None:
            plan.comments.append((at, HEAD, rng.choice(COMMENTS_HEAD)))
    names = [name.format(short=short) for name in FILE_NAMES]
    rng.shuffle(names)
    for _ in range(rng.randint(*persona.files)):
        at = _uniform_time(rng, max(plan.created_at + timedelta(hours=3), window_start), end)
        if at is not None and names:
            plan.files.append((at, plan.kam, names.pop()))
    plan.comments.sort()
    plan.files.sort()


def _amount(rng: random.Random, pipeline: str) -> Decimal:
    if pipeline == "b2b":
        return Decimal(rng.randrange(150_000, 1_500_000, 10_000))
    return Decimal(rng.randrange(45_000, 180_000, 5_000))


def _person_name(rng: random.Random, first_m, first_f, last, mid_m, mid_f) -> str:  # noqa: ANN001
    if rng.random() < 0.5:
        return f"{rng.choice(last)} {rng.choice(first_m)} {rng.choice(mid_m)}"
    return f"{rng.choice(last)}а {rng.choice(first_f)} {rng.choice(mid_f)}"


def build_activity_plan(now: datetime) -> ActivityPlanSet:
    """Детерминированный план демо-активности относительно `now` (без БД)."""
    rng = random.Random(PLAN_SEED)
    window = activity_window(now)
    window_start, _ = window
    left_at = window_start - timedelta(days=LEFT_AT_DAYS_BEFORE_WINDOW)

    universities_by_kam: dict[str, list[str]] = {}
    for short, kam in BASE_UNIVERSITIES.items():
        universities_by_kam.setdefault(kam, []).append(short)
    for _name, short, _region, _city, kam in NEW_UNIVERSITIES:
        if short not in UNTOUCHED_UNIVERSITIES:
            universities_by_kam.setdefault(kam, []).append(short)

    clients: list[ClientPlan] = []
    names_seen: set[str] = set()
    while len(clients) < CLIENTS_COUNT:
        full_name = _person_name(
            rng, _CLIENT_FIRST_M, _CLIENT_FIRST_F, _CLIENT_LAST, _CLIENT_MID_M, _CLIENT_MID_F
        )
        if full_name in names_seen:
            continue
        names_seen.add(full_name)
        n = len(clients) + 1
        clients.append(
            ClientPlan(
                full_name,
                f"client{n:02d}@example.com",
                f"+7 917 {n:03d}-{n * 7 % 100:02d}-{n * 13 % 100:02d}",
            )
        )

    deals: list[DealPlan] = []
    titles: set[str] = set()
    client_cursor = 0

    def unique(title: str) -> str:
        candidate, n = title, 2
        while candidate in titles:
            candidate, n = f"{title} ({n})", n + 1
        titles.add(candidate)
        return candidate

    for username, _full_name, persona_code, _active in KAMS:
        persona = PERSONAS[persona_code]
        own_unis = universities_by_kam.get(username, [])
        b2b_count = EXISTING_KAM_EXTRA.get(username, persona.b2b)
        b2c_count = int(persona.b2c) + (1 if rng.random() < persona.b2c % 1 else 0)
        if username in EXISTING_KAM_EXTRA:
            b2c_count = 0
        topics = list(TOPICS_B2B)
        rng.shuffle(topics)
        for index in range(b2b_count + b2c_count):
            pipeline = "b2b" if index < b2b_count else "b2c"
            if pipeline == "b2b" and not own_unis:
                continue
            if persona_code == "left":
                created = left_at - timedelta(days=rng.uniform(70, 160))
                limit = left_at
                outcome = rng.choice(("won", "lost", "won"))
            elif persona_code == "idle":
                created = window_start - timedelta(days=rng.uniform(50, 140))
                limit = window_start - timedelta(days=5)
                outcome = "open"
            else:
                recent = rng.random() < persona.recent
                created = (
                    now - timedelta(days=rng.uniform(2, (now - window_start).days + 50))
                    if recent
                    else now - timedelta(days=rng.uniform(110, 300))
                )
                limit = now - timedelta(hours=6)
                outcome = rng.choices(("won", "lost", "open"), weights=(25, 15, 60))[0]
                if not recent and outcome == "open":
                    # старая заявка активного КАМа не «висит» полгода на одном этапе:
                    # она давно закрыта (иначе зависшими были бы все старые заявки)
                    outcome = "won" if rng.random() < 0.6 else "lost"
            route = _route(pipeline, outcome, rng)
            if pipeline == "b2b":
                short = own_unis[index % len(own_unis)]
                topic, product, program = topics[index % len(topics)]
                title = unique(f"{short} — {topic}")
                plan = DealPlan(
                    title=title,
                    pipeline="b2b",
                    kam=username,
                    university=short,
                    client=None,
                    product=product,
                    program=program,
                    amount=_amount(rng, "b2b"),
                    steps=[],
                )
                bodies, lost = COMMENTS_B2B, LOST_REASONS_B2B
            else:
                client = clients[client_cursor % len(clients)]
                client_cursor += 1
                program, product = rng.choice(TOPICS_B2C)
                plan = DealPlan(
                    title=unique(f"{display_name_from_full_name(client.full_name)} — {program}"),
                    pipeline="b2c",
                    kam=username,
                    university=None,
                    client=client.email,
                    product=product,
                    program=program,
                    amount=_amount(rng, "b2c"),
                    steps=[],
                )
                short, bodies, lost = "клиент", COMMENTS_B2C, LOST_REASONS_B2C
            plan.steps = _simulate(
                rng,
                pipeline=pipeline,
                route=route,
                created=created,
                dwell=persona.dwell,
                limit=limit,
                kam=username,
                lost_reasons=lost,
                close_limit=now - timedelta(days=CLOSE_GAP_DAYS),
            )
            if plan.steps[-1].stage not in _TERMINAL and persona_code not in ("left", "idle"):
                plan.steps = _keep_open_deal_fresh(rng, plan.steps, persona.dwell, now)
            hard_limit = limit if persona_code in ("left", "idle") else now
            _plan_interactions(
                rng,
                plan,
                persona,
                window=window,
                hard_limit=hard_limit,
                bodies=bodies,
                short=short,
            )
            deals.append(plan)

    for short, topic, kam, age, target, on_target in HISTORY_DEALS_B2B:
        created = now - timedelta(days=age)
        reached = now - timedelta(days=on_target)
        route = _route("b2b", "won", rng)
        if target == "rejected":
            route = ["new", "first_contact", "negotiation", "rejected"]
        else:
            route = route[: route.index(target) + 1]
        step = (reached - created) / max(len(route) - 1, 1)
        steps = [StepPlan(route[0], created, kam)]
        for index in range(1, len(route)):
            pair = (route[index - 1], route[index])
            actor = HEAD if pair in _HEAD_ONLY else kam
            comment = LOST_REASONS_B2B[0] if route[index] == "rejected" else None
            at = reached if index == len(route) - 1 else created + step * index
            steps.append(StepPlan(route[index], at, actor, comment=comment))
        plan = DealPlan(
            title=unique(f"{short} — {topic}"),
            pipeline="b2b",
            kam=kam,
            university=short,
            client=None,
            product="corp-edu" if "ППС" in topic or "стажировки" in topic else "dpo-ai",
            program=None,
            amount=_amount(rng, "b2b"),
            steps=steps,
        )
        for _ in range(rng.randint(1, 3)):
            at = _uniform_time(rng, created + timedelta(days=1), reached)
            if at is not None:
                plan.comments.append((at, kam, rng.choice(COMMENTS_B2B)))
        plan.comments = sorted({body: (at, a, body) for at, a, body in plan.comments}.values())
        deals.append(plan)

    activities: list[ActivityPlan] = []
    activity_names: set[str] = set()
    for name, kind, short, days in PAST_ACTIVITIES:
        kam = BASE_UNIVERSITIES[short]
        activities.append(
            ActivityPlan(name, kind, short, kam, ((now - timedelta(days=days), 3, "offline"),))
        )
        activity_names.add(name)
    for username, _full_name, persona_code, _active in KAMS:
        persona = PERSONAS[persona_code]
        own_unis = universities_by_kam.get(username, [])
        for index in range(persona.events):
            if not own_unis:
                break
            short = own_unis[(index + 1) % len(own_unis)]
            kind, label = rng.choice(EVENT_KINDS)
            name = f"{label} — {short}"
            if name in activity_names:
                continue
            activity_names.add(name)
            first = _uniform_time(rng, window_start + timedelta(days=2), now - timedelta(days=1))
            if first is None:
                continue
            slots = [(first, rng.choice((2, 3, 4)), rng.choice(("offline", "online", "hybrid")))]
            if rng.random() < 0.3:
                slots.append((now + timedelta(days=rng.uniform(7, 21)), 3, "offline"))
            activities.append(ActivityPlan(name, kind, short, username, tuple(slots)))

    students: list[StudentPlan] = []
    student_unis = list(BASE_UNIVERSITIES) + [
        short for _n, short, *_rest in NEW_UNIVERSITIES if short not in UNTOUCHED_UNIVERSITIES
    ]
    seen_students: set[str] = set()
    while len(students) < STUDENTS_COUNT:
        full_name = _person_name(
            rng, _STUDENT_FIRST_M, _STUDENT_FIRST_F, _STUDENT_LAST, _STUDENT_MID_M, _STUDENT_MID_F
        )
        if full_name in seen_students:
            continue
        seen_students.add(full_name)
        n = len(students) + 1
        moment = now - timedelta(days=rng.uniform(45, 260))
        path = [("candidate", moment)]
        gaps = {"studying": (10, 40), "graduate": (45, 110), "talent_pool": (5, 35)}
        for status in ("studying", "graduate", "talent_pool"):
            if rng.random() < 0.15:
                break
            moment += timedelta(days=rng.uniform(*gaps[status]))
            if moment >= now - timedelta(hours=12):
                break
            path.append((status, moment))
        students.append(
            StudentPlan(
                full_name=full_name,
                email=f"talent{n:02d}@example.com",
                university=student_unis[(n * 5) % len(student_unis)],
                program=STUDENT_PROGRAMS[n % len(STUDENT_PROGRAMS)],
                path=tuple(path),
            )
        )

    return ActivityPlanSet(
        window_start=window[0],
        window_end=window[1],
        kams=list(KAMS),
        universities=list(NEW_UNIVERSITIES),
        clients=clients,
        deals=deals,
        activities=activities,
        students=students,
    )


# --------------------------------------------------------------------------------------
# Запись в БД (идемпотентно)
# --------------------------------------------------------------------------------------


@dataclass
class _Files:
    """Состояние загрузки файлов: при первой ошибке MinIO шаг выключается целиком."""

    enabled: bool = True
    stored: int = 0
    warned: bool = False


async def _ensure_kams(session: AsyncSession, users: Mapping[str, AppUser]) -> dict[str, AppUser]:
    result: dict[str, AppUser] = dict(users)
    head = result.get(HEAD)
    for username, full_name, _persona, active in KAMS:
        user = result.get(username)
        if user is None:
            user = (
                await session.execute(
                    select(AppUser).where(func.lower(AppUser.username) == username.lower())
                )
            ).scalar_one_or_none()
        if user is None:
            user = AppUser(
                keycloak_sub=_demo_sub(username),
                username=username,
                email=username.replace("@demo", "@crm.local"),
                full_name=full_name,
                roles=["kam"],
                manager_id=head.id if head else None,
                is_active=active,
            )
            session.add(user)
            await session.flush()
        result[username] = user
    return result


async def _ensure_universities(
    session: AsyncSession, universities: Mapping[str, University], users: Mapping[str, AppUser]
) -> dict[str, University]:
    result: dict[str, University] = dict(universities)
    for name, short, region, city, kam in NEW_UNIVERSITIES:
        row = (
            await session.execute(
                select(University).where(func.lower(University.name) == name.lower())
            )
        ).scalar_one_or_none()
        if row is None:
            row = University(
                name=name,
                short_name=short,
                region=region,
                city=city,
                kam_user_id=users[kam].id,
            )
            session.add(row)
            await session.flush()
        elif row.kam_user_id is None:
            row.kam_user_id = users[kam].id
        result[short] = row
    return result


async def _ensure_clients(
    session: AsyncSession, plans: Sequence[ClientPlan]
) -> dict[str, Counterparty]:
    crypto = get_pii_crypto()
    result: dict[str, Counterparty] = {}
    for plan in plans:
        email_hmac = crypto.email_hmac(plan.email)
        row = (
            await session.execute(select(Counterparty).where(Counterparty.email_hmac == email_hmac))
        ).scalar_one_or_none()
        if row is None:
            row = Counterparty(
                kind="person",
                display_name=display_name_from_full_name(plan.full_name),
                full_name=plan.full_name,
                email=plan.email,
                phone=plan.phone,
                email_hmac=email_hmac,
                full_name_hmac=crypto.full_name_hmac(plan.full_name),
            )
            session.add(row)
            await session.flush()
        result[plan.email] = row
    return result


async def _transitions(
    session: AsyncSession, workflow: Workflow
) -> dict[tuple[uuid.UUID, uuid.UUID], WorkflowTransition]:
    rows = (
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
    return {(t.from_stage_id, t.to_stage_id): t for t in rows}


def _file_payload(name: str, title: str) -> tuple[bytes, str]:
    content_type = {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }.get(name.rsplit(".", 1)[-1], "application/octet-stream")
    body = f"Демо-файл «{name}» к заявке «{title}» (сид отчёта «Активность КАМов»).\n"
    return body.encode("utf-8"), content_type


async def _store_file(
    session: AsyncSession,
    state: _Files,
    *,
    deal: Deal,
    uploader: AppUser,
    name: str,
    at: datetime,
) -> None:
    if not state.enabled:
        return
    exists = (
        await session.execute(
            select(FileObject.id).where(
                FileObject.entity_type == "deal",
                FileObject.entity_id == deal.id,
                FileObject.original_name == name,
            )
        )
    ).first()
    if exists is not None:
        return
    from app.modules.files.service import store_bytes

    data, content_type = _file_payload(name, deal.title)
    try:
        file_object = await store_bytes(
            session,
            data=data,
            original_name=name,
            content_type=content_type,
            bucket=get_settings().s3_bucket_files,
            entity_type="deal",
            entity_id=deal.id,
            uploaded_by=uploader.id,
        )
    except Exception as exc:  # noqa: BLE001 — MinIO недоступен: сид продолжается без файлов
        state.enabled = False
        if not state.warned:
            state.warned = True
            print(f"seed: файлы заявок пропущены — MinIO недоступен ({type(exc).__name__})")  # noqa: T201
        return
    file_object.created_at = at
    # файл «в статусе» (миграция 0006): строка истории, действовавшая на момент загрузки;
    # бэкфилл 0006 идёт при миграции, а сид — после неё, поэтому привязка здесь
    file_object.stage_history_id = (
        await session.execute(
            select(DealStageHistory.id)
            .where(DealStageHistory.deal_id == deal.id, DealStageHistory.created_at <= at)
            .order_by(DealStageHistory.created_at.desc(), DealStageHistory.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    state.stored += 1


async def _create_deal(
    session: AsyncSession,
    plan: DealPlan,
    *,
    workflow: Workflow,
    stages: Mapping[str, WorkflowStage],
    transitions: Mapping[tuple[uuid.UUID, uuid.UUID], WorkflowTransition],
    users: Mapping[str, AppUser],
    universities: Mapping[str, University],
    clients: Mapping[str, Counterparty],
    products: Mapping[str, Product],
    programs: Mapping[str, Program],
    interaction_type_id: uuid.UUID | None,
    files: _Files,
) -> bool:
    if (await session.execute(select(Deal.id).where(Deal.title == plan.title))).first():
        return False
    if any(step.stage not in stages for step in plan.steps):
        return False  # этап удалён из схемы (демо перевода) — заявку не создаём
    if plan.pipeline == "b2b" and plan.university not in universities:
        return False
    responsible = users[plan.kam]
    last = plan.steps[-1]
    target = stages[last.stage]
    status = target.terminal_outcome if target.is_terminal and target.terminal_outcome else "open"
    moments = [last.at] + [at for at, _a, _b in plan.comments] + [at for at, _a, _n in plan.files]
    product = products.get(plan.product) if plan.product else None
    program = programs.get(plan.program) if plan.program else None
    deal = Deal(
        pipeline=workflow.code,
        workflow_id=workflow.id,
        stage_id=target.id,
        title=plan.title,
        university_id=universities[plan.university].id if plan.university else None,
        counterparty_id=clients[plan.client].id if plan.client else None,
        interaction_type_id=interaction_type_id if plan.pipeline == "b2c" else None,
        product_id=product.id if product else None,
        program_id=program.id if program else None,
        responsible_user_id=responsible.id,
        amount=plan.amount,
        status=status,
        stage_entered_at=last.at,
        last_activity_at=max(moments),
        created_by=responsible.id,
        created_at=plan.created_at,
        updated_at=max(moments),
    )
    session.add(deal)
    await session.flush()

    previous: WorkflowStage | None = None
    for index, step in enumerate(plan.steps):
        stage = stages[step.stage]
        transition = transitions.get((previous.id, stage.id)) if previous else None
        actor = users.get(step.actor) if step.actor else None
        session.add(
            DealStageHistory(
                deal_id=deal.id,
                from_stage_id=previous.id if previous else None,
                to_stage_id=stage.id,
                from_stage_name=previous.name if previous else None,
                to_stage_name=stage.name,
                transition_id=transition.id if transition else None,
                is_return=step.is_return,
                actor_user_id=actor.id if actor else None,
                kind=step.kind,
                reason="created" if index == 0 else step.reason,
                comment=step.comment,
                created_at=step.at,
            )
        )
        previous = stage
    for at, author, body in plan.comments:
        session.add(
            DealComment(deal_id=deal.id, author_user_id=users[author].id, body=body, created_at=at)
        )
    await session.flush()
    for at, uploader, name in plan.files:
        await _store_file(session, files, deal=deal, uploader=users[uploader], name=name, at=at)
    return True


async def _base_deal_extras(
    session: AsyncSession, users: Mapping[str, AppUser], files: _Files, *, now: datetime
) -> tuple[int, int]:
    """Комментарии и файлы к заявкам базового сида (по заголовку)."""
    comments = 0
    titles = {title for title, *_rest in BASE_DEAL_COMMENTS} | {t for t, *_r in BASE_DEAL_FILES}
    deals = {
        deal.title: deal
        for deal in (await session.execute(select(Deal).where(Deal.title.in_(titles)))).scalars()
    }
    for title, days, author, body in BASE_DEAL_COMMENTS:
        deal = deals.get(title)
        if deal is None:
            continue
        exists = (
            await session.execute(
                select(DealComment.id).where(
                    DealComment.deal_id == deal.id, DealComment.body == body
                )
            )
        ).first()
        if exists is not None:
            continue
        at = max(now - timedelta(days=days), deal.created_at + timedelta(hours=1))
        author_user = (
            users[author] if author else await session.get(AppUser, deal.responsible_user_id)
        )
        if author_user is None:
            continue
        session.add(
            DealComment(deal_id=deal.id, author_user_id=author_user.id, body=body, created_at=at)
        )
        comments += 1
    await session.flush()
    for title, days, name in BASE_DEAL_FILES:
        deal = deals.get(title)
        if deal is None:
            continue
        uploader = await session.get(AppUser, deal.responsible_user_id)
        if uploader is None:
            continue
        at = max(now - timedelta(days=days), deal.created_at + timedelta(hours=2))
        await _store_file(session, files, deal=deal, uploader=uploader, name=name, at=at)
    return comments, files.stored


async def _ensure_activities(
    session: AsyncSession,
    plans: Sequence[ActivityPlan],
    users: Mapping[str, AppUser],
    universities: Mapping[str, University],
) -> int:
    created = 0
    for plan in plans:
        if plan.university not in universities:
            continue
        exists = (
            await session.execute(select(Activity.id).where(Activity.name == plan.name))
        ).first()
        if exists is not None:
            continue
        activity = Activity(
            name=plan.name,
            activity_type=plan.activity_type,
            university_id=universities[plan.university].id,
            responsible_user_id=users[plan.kam].id,
            description="Мероприятие КАМа с вузом (демо-история отчёта «Активность КАМов»)",
        )
        session.add(activity)
        await session.flush()
        for starts_at, hours, fmt in plan.slots:
            session.add(
                ActivitySchedule(
                    activity_id=activity.id,
                    starts_at=starts_at,
                    ends_at=starts_at + timedelta(hours=hours) if hours else None,
                    format=fmt,
                    location="Онлайн" if fmt == "online" else "Кампус вуза",
                )
            )
        created += 1
    await session.flush()
    return created


async def _ensure_students(
    session: AsyncSession,
    plans: Sequence[StudentPlan],
    universities: Mapping[str, University],
    programs: Mapping[str, Program],
    admin: AppUser | None,
) -> int:
    crypto = get_pii_crypto()
    created = 0
    for plan in plans:
        email_hmac = crypto.email_hmac(plan.email)
        exists = (
            await session.execute(select(Student.id).where(Student.email_hmac == email_hmac))
        ).first()
        if exists is not None:
            continue
        program = programs.get(plan.program)
        university = universities.get(plan.university)
        status, changed_at = plan.path[-1]
        student = Student(
            full_name=plan.full_name,
            email=plan.email,
            email_hmac=email_hmac,
            full_name_hmac=crypto.full_name_hmac(plan.full_name),
            display_name=display_name_from_full_name(plan.full_name),
            university_id=university.id if university else None,
            product_id=program.product_id if program else None,
            program_id=program.id if program else None,
            funnel_status=status,
            status_changed_at=changed_at,
            created_by=admin.id if admin else None,
            created_at=plan.path[0][1],
        )
        session.add(student)
        await session.flush()
        previous: str | None = None
        for to_status, at in plan.path:
            session.add(
                StudentStatusHistory(
                    student_id=student.id,
                    from_status=previous,
                    to_status=to_status,
                    actor_user_id=admin.id if admin else None,
                    created_at=at,
                )
            )
            previous = to_status
        created += 1
    await session.flush()
    return created


CONTRACT_SIGNED_STAGE = "contract_signed"
CONTRACT_VALID_DAYS = 365


@dataclass(frozen=True)
class ContractPlan:
    number: str
    university: str  # короткое имя вуза
    kam: str
    product: str | None
    amount: Decimal | None
    signed_at: datetime


def plan_contracts(plan: ActivityPlanSet, now: datetime) -> list[ContractPlan]:
    """Договоры вузов по плану: первая B2B-заявка вуза, вошедшая в «Договор подписан» не
    позже `now`, даёт договор, подписанный в момент этого перехода. Номера — по порядку
    подписания (`Д-<год>/2NN`, вне диапазона номеров базового сида)."""
    first: dict[str, tuple[datetime, DealPlan]] = {}
    for deal in plan.deals:
        if deal.pipeline != "b2b" or deal.university is None:
            continue
        signed = next(
            (s.at for s in deal.steps if s.stage == CONTRACT_SIGNED_STAGE and s.at <= now),
            None,
        )
        if signed is None:
            continue
        known = first.get(deal.university)
        if known is None or signed < known[0]:
            first[deal.university] = (signed, deal)
    ordered = sorted(first.items(), key=lambda item: (item[1][0], item[0]))
    return [
        ContractPlan(
            number=f"Д-{signed.year}/{200 + index:03d}",
            university=university,
            kam=deal.kam,
            product=deal.product,
            amount=deal.amount,
            signed_at=signed,
        )
        for index, (university, (signed, deal)) in enumerate(ordered, start=1)
    ]


async def _ensure_contracts(
    session: AsyncSession,
    contracts: Sequence[ContractPlan],
    users: Mapping[str, AppUser],
    universities: Mapping[str, University],
    products: Mapping[str, Product],
) -> int:
    """Действующие договоры вузов из плана (идемпотентно по номеру; у вуза уже есть
    договор — пропуск)."""
    created = 0
    admin = users.get(ADMIN)
    for spec in contracts:
        university = universities.get(spec.university)
        if university is None:
            continue
        exists = (
            await session.execute(
                select(Contract.id).where(
                    (func.lower(Contract.number) == spec.number.lower())
                    | (Contract.university_id == university.id)
                )
            )
        ).first()
        if exists is not None:
            continue
        signed = spec.signed_at.astimezone(MSK).date()
        contract = Contract(
            number=spec.number,
            university_id=university.id,
            status="active",
            signed_at=signed,
            valid_from=signed,
            valid_to=signed + timedelta(days=CONTRACT_VALID_DAYS),
            amount=spec.amount,
            responsible_user_id=users[spec.kam].id if spec.kam in users else None,
        )
        session.add(contract)
        await session.flush()
        product = products.get(spec.product) if spec.product else None
        if product is not None:
            session.add(
                ContractProduct(
                    contract_id=contract.id,
                    product_id=product.id,
                    added_by=admin.id if admin else None,
                )
            )
        created += 1
    await session.flush()
    return created


async def seed_activity_history(
    session: AsyncSession,
    workflows: Mapping[str, tuple[Workflow, Mapping[str, WorkflowStage]]],
    universities: Mapping[str, University],
    products: Mapping[str, Product],
    programs: Mapping[str, Program],
    users: Mapping[str, AppUser],
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    """Дополняет стенд историей активности ~20 КАМов (идемпотентно, без commit)."""
    now = now or demo_now()
    plan = build_activity_plan(now)
    all_users = await _ensure_kams(session, users)
    all_universities = await _ensure_universities(session, universities, all_users)
    clients = await _ensure_clients(session, plan.clients)
    interaction_type_id = (
        await session.execute(
            select(InteractionType.id).where(
                InteractionType.pipeline == "b2c", InteractionType.code == "dpo_course"
            )
        )
    ).scalar_one_or_none()
    files = _Files()
    transitions = {
        code: await _transitions(session, wf) for code, (wf, _stages) in workflows.items()
    }

    deals = 0
    for deal_plan in plan.deals:
        if deal_plan.pipeline not in workflows:
            continue
        workflow, stages = workflows[deal_plan.pipeline]
        if await _create_deal(
            session,
            deal_plan,
            workflow=workflow,
            stages=stages,
            transitions=transitions[deal_plan.pipeline],
            users=all_users,
            universities=all_universities,
            clients=clients,
            products=products,
            programs=programs,
            interaction_type_id=interaction_type_id,
            files=files,
        ):
            deals += 1
    base_comments, _ = await _base_deal_extras(session, all_users, files, now=now)
    activities = await _ensure_activities(session, plan.activities, all_users, all_universities)
    students = await _ensure_students(
        session, plan.students, all_universities, programs, all_users.get(ADMIN)
    )
    contracts = await _ensure_contracts(
        session, plan_contracts(plan, now), all_users, all_universities, products
    )
    await session.flush()
    return {
        "kams": len(KAMS),
        "deals": deals,
        "contracts": contracts,
        "base_comments": base_comments,
        "files": files.stored,
        "activities": activities,
        "students": students,
    }


# --------------------------------------------------------------------------------------
# Самостоятельный запуск поверх уже засеянного стенда
# --------------------------------------------------------------------------------------


async def _load_context(
    session: AsyncSession,
) -> tuple[
    dict[str, tuple[Workflow, dict[str, WorkflowStage]]],
    dict[str, University],
    dict[str, Product],
    dict[str, Program],
    dict[str, AppUser],
]:
    """Только чтение: схемы воронок не пересеиваются (удалённые этапы остаются удалёнными)."""
    workflows: dict[str, tuple[Workflow, dict[str, WorkflowStage]]] = {}
    for code in ("b2b", "b2c"):
        workflow = (
            await session.execute(select(Workflow).where(Workflow.code == code))
        ).scalar_one_or_none()
        if workflow is None:
            continue
        stages = (
            (
                await session.execute(
                    select(WorkflowStage).where(
                        WorkflowStage.workflow_id == workflow.id, WorkflowStage.deleted_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        workflows[code] = (workflow, {stage.code: stage for stage in stages})
    universities = {
        row.short_name: row
        for row in (await session.execute(select(University))).scalars()
        if row.short_name
    }
    products = {row.code: row for row in (await session.execute(select(Product))).scalars()}
    programs = {row.name: row for row in (await session.execute(select(Program))).scalars()}
    users = {row.username: row for row in (await session.execute(select(AppUser))).scalars()}
    return workflows, universities, products, programs, users


def _workflow_seed_codes() -> dict[str, list[str]]:
    """Коды этапов из JSON-сидов схем (для самопроверки плана в тестах)."""
    return {
        code: [
            stage["code"]
            for stage in json.loads((SEEDS_DIR / f"{code}.json").read_text("utf-8"))["stages"]
        ]
        for code in ("b2b", "b2c")
    }


async def run_seed_activity() -> int:
    from app.core.db import get_session_factory

    get_pii_crypto()  # ранний fail с понятной ошибкой, если PII-ключи не заданы
    async with get_session_factory()() as session:
        workflows, universities, products, programs, users = await _load_context(session)
        if not workflows or HEAD not in users or "kam1@demo" not in users:
            print("seed_activity: сначала базовый сид — python -m app.tools.seed")  # noqa: T201
            return 1
        stats = await seed_activity_history(
            session, workflows, universities, products, programs, users
        )
        await session.commit()
    print(  # noqa: T201
        "seed_activity: КАМов {kams}, новых заявок {deals}, "
        "комментариев к базовым {base_comments}, файлов {files}, "
        "мероприятий {activities}, студентов {students}".format(**stats)
    )
    return 0


def main() -> int:
    from app.core.db import dispose_engine

    async def _amain() -> int:
        try:
            return await run_seed_activity()
        finally:
            await dispose_engine()

    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "ActivityPlanSet",
    "build_activity_plan",
    "activity_window",
    "seed_activity_history",
    "run_seed_activity",
]
