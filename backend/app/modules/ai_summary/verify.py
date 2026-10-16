"""Проверка ответа модели кодом, а не только промптом: «не придумывает числа и не советует».

Каждая фраза (пункт раздела; заголовок — по предложениям) проходит по порядку:
1. псевдонимы: `КАМ-1`, `КАМ‑01`, `кам-1`, `КАМ 1:`, `(КАМ 1)` → `КАМ-01` (без дефиса —
   только перед «:», «)», «,», «;», тире или в конце: «у КАМ 3 заявки» — не псевдоним);
   псевдонима нет среди выданных — фраза удаляется;
2. ссылки и разметка (`http`, `www.`, `<`, `>`) — удаляется;
3. советы — `NO_ADVICE_RE` отчёта «Просрочки» и шире `SUMMARY_ADVICE_RE` («целесообразно»,
   «предлагаем», «важно», «надо», «стоит обсудить», «пересмотреть», «Как ускорить…?») —
   удаляется, `advice_removed += 1`; названия из фактов в кавычках не проверяются;
4. оценки людей и работы (`EVALUATIVE_RE`: «не справляется», «слабый», «неэффективно»…) —
   удаляется; утверждение про КАМа (с псевдонимом, кроме «Вопросов») без единого числа —
   тоже: про человека — только факты с цифрами;
5. пункт «Вопросы» без «?» в конце — удаляется;
6. числа: псевдонимы, `B2B`/`B2C` и цитаты («…», "…") с названиями из фактов вырезаются; даты
   (день 1–31, месяц 1–12; «26.67» — дробь, а не дата) сверяются с датами фактов, остальные
   числа — с допустимым множеством: числовые листья фактов (по модулю; дробные — с допуском
   0,05 и округлением до целого), числа внутри строк фактов (подписи корзин, правило
   отбора), длины списков, дни и годы периода. Во фразе с псевдонимом — только строки этих
   КАМов (`activity.kams`, `overdue.kams`) и общие цифры, без строк других КАМов. Любое
   число вне множества — фраза удаляется;
7. пункт длиннее 300 символов — удаляется.

Это сверка «числа есть в данных отчёта», а не доказательство, что метрика названа верно:
перепутанные общие цифры проверка не поймает, поэтому плашка «сверяйте цифры» остаётся.

Провал (`llm_bad_output`): удалён заголовок, удалено больше половины или после удалений
осталось меньше двух фраз (короткий ответ без удалений — не провал: за пустой период модель
законно отвечает одной фразой). Сводка по правилам проходит ту же проверку без удалений.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.modules.ai_summary.parse import SECTION_LIMITS
from app.modules.reporting.sla import NO_ADVICE_RE

LIST_SECTIONS: tuple[str, ...] = tuple(SECTION_LIMITS)
PHRASE_MAX = 300
TOLERANCE = 0.05 + 1e-9

# «КАМ-3», «КАМ‑01», «кам–1» — всегда; «КАМ 3» / «КАМ3» — только перед «:», «)», «]», «,»,
# «;», «!», «?», тире или в конце фразы («У одного КАМ 3 заявки» — обычная речь)
ALIAS_RE = re.compile(
    r"(?<![\w])КАМ(?:[\-‐‑–—]\s?0?(\d{1,2})(?!\d)"
    r"|\s?0?(\d{1,2})(?!\d)(?=\s*(?:[:)\],;!?]|$)|\.(?!\d)|\s+[—–-]\s))",
    re.IGNORECASE,
)
CANONICAL_ALIAS_RE = re.compile(r"КАМ-\d{2}")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[А-ЯЁA-Z«\"(])")
LINK_RE = re.compile(r"https?:|www\.|[<>]", re.IGNORECASE)
WORKFLOW_RE = re.compile(r"\bB2[BC]\b", re.IGNORECASE)
# «ёлочки», а также "лапки" и 'апострофы' — GigaChat пишет названия этапов по-разному
QUOTE_RE = re.compile(r"«([^«»]*)»|\"([^\"]*)\"|'([^']*)'|“([^“”]*)”")
# дата ДД.ММ[.ГГГГ] с допустимыми днём и месяцем: «26.67%» и «15.25 дн.» — дроби
DATE_RE = re.compile(
    r"(?<![\d.,])(0?[1-9]|[12]\d|3[01])\.(0[1-9]|1[0-2])(?:\.(\d{4}))?(?![\d])(?!,\d)"
)
# советы сводки шире, чем у отчёта «Просрочки» (тот не трогаем): эксперты кейсодержателя
# просили не давать рекомендаций по процессам — ни прямых, ни мягких, ни вопросом «Как…?»
SUMMARY_ADVICE_RE = re.compile(
    r"\b(?:целесообразн\w*|предлага\w*|предлож(?:ить|им|ите)|совету\w*|важно|желательно|"
    r"надо|требуется|необходим\w*|долж(?:ен|на|но|ны)|имеет\s+смысл|стоит\s+ли|"
    r"стоило\s+бы|следовало|(?:можно|стоит|лучше|нужно|следует)\s+(?:бы\s+)?\w+(?:ть|ти|чь)(?:ся)?|"
    r"пересмотр\w*|перераспредел(?:ить|ите|ять|яйте|ение|ения)|"
    r"(?:ускор|улучш|сократ|повыс|сниз|усил|упрост|внедр)(?:ить|ите|ьте|ять|яйте)|"
    r"оптимизир(?:овать|уйте)|автоматизир(?:овать|уйте)|"
    r"обратите|уделите|проверьте|рассмотрите|назначьте|свяжитесь|организуйте|проведите|"
    r"как\s+(?:ускорить|улучшить|сократить|повысить|снизить|избежать|исправить|изменить|"
    r"оптимизировать))\b",
    re.IGNORECASE,
)
# оценки людей и работы: сводка — факты, а после подстановки ФИО такая фраза читалась бы
# как характеристика конкретного сотрудника
EVALUATIVE_RE = re.compile(
    r"\b(?:не\s+справля\w*|плох\w*|слаб\w*|неэффективн\w*|халатн\w*|безответственн\w*|"
    r"некомпетентн\w*|ленив\w*|недобросовестн\w*|недоработ\w*|провал\w*|худш\w*|"
    r"ужасн\w*|отвратительн\w*|разочаров\w*|игнорир\w*|саботир\w*|бездейству\w*)",
    re.IGNORECASE,
)
# причина удаления → группа для подписи сверки (`checks.removed_by_reason`)
REASON_GROUPS: dict[str, str] = {
    "numbers": "numbers",
    "advice": "advice",
    "evaluative": "evaluative",
    "person_without_numbers": "evaluative",
}
REMOVED_GROUPS: tuple[str, ...] = ("numbers", "advice", "evaluative", "other")
NUMBER_RE = re.compile(r"(?<![\w.,])[-−+]?(?:\d{1,3}(?:[   ]\d{3})+|\d+)(?:[.,]\d+)?(?![\w])")


@dataclass(frozen=True)
class Verified:
    """Разделы после проверки (с псевдонимами) и счётчики `checks`."""

    sections: dict[str, Any]
    checks: dict[str, Any]


class VerificationFailed(Exception):
    def __init__(self, reason: str, checks: Mapping[str, Any]) -> None:
        super().__init__(reason)
        self.reason = reason
        self.checks = dict(checks)


@dataclass
class _Allowed:
    integers: set[int] = field(default_factory=set)
    fractions: list[float] = field(default_factory=list)
    dates: set[str] = field(default_factory=set)
    texts: set[str] = field(default_factory=set)

    def add_number(self, value: float) -> None:
        value = abs(float(value))
        if not math.isfinite(value):
            return
        if value.is_integer():
            self.integers.add(int(value))
            return
        self.fractions.append(value)
        # дробное можно написать с одним знаком или округлить до целого (оба правила)
        self.fractions.append(round(value, 1))
        self.integers.add(round(value))
        self.integers.add(math.floor(value + 0.5))

    def matches(self, value: float) -> bool:
        value = abs(value)
        if value.is_integer() and int(value) in self.integers:
            return True
        return any(abs(value - allowed) <= TOLERANCE for allowed in self.fractions)

    def merged(self, *others: _Allowed) -> _Allowed:
        result = _Allowed(
            set(self.integers), list(self.fractions), set(self.dates), set(self.texts)
        )
        for other in others:
            result.integers |= other.integers
            result.fractions.extend(other.fractions)
            result.dates |= other.dates
            result.texts |= other.texts
        return result


@dataclass
class AllowedFacts:
    """Допустимые значения: общие (`general`), строки КАМов по псевдонимам и всё вместе."""

    general: _Allowed
    by_alias: dict[str, _Allowed]
    full: _Allowed

    @property
    def texts(self) -> set[str]:
        return self.full.texts

    def matches(self, value: float) -> bool:
        return self.full.matches(value)

    def for_aliases(self, aliases: Iterable[str]) -> _Allowed:
        """Фраза с псевдонимами: общие цифры и строки только этих КАМов."""
        rows = [self.by_alias[alias] for alias in dict.fromkeys(aliases) if alias in self.by_alias]
        return self.general.merged(*rows)


# --------------------------------------------------------------------------------------
# Допустимые числа и даты
# --------------------------------------------------------------------------------------


def _date_key(day: str, month: str, year: str | None) -> str:
    key = f"{int(day):02d}.{month}"
    return f"{key}.{year}" if year else key


def _parse_number(token: str) -> float | None:
    cleaned = (
        token.replace(" ", "").replace(" ", "").replace(" ", "").replace(",", ".").replace("−", "-")
    )
    try:
        return float(cleaned)
    except ValueError:
        return None


def _absorb_text(text: str, allowed: _Allowed) -> None:
    allowed.texts.add(text.strip().casefold())
    # номера псевдонимов и метки процессов — не числа отчёта
    text = WORKFLOW_RE.sub(" ", CANONICAL_ALIAS_RE.sub(" ", text))
    for match in DATE_RE.finditer(text):
        day, month, year = match.groups()
        allowed.dates.add(_date_key(day, month, year))
        allowed.dates.add(_date_key(day, month, None))
        if year:
            allowed.add_number(int(year))
    rest = DATE_RE.sub(" ", text)
    for match in NUMBER_RE.finditer(rest):
        number = _parse_number(match.group(0))
        if number is not None:
            allowed.add_number(number)


def _row_alias(item: Any) -> str | None:
    """Строка КАМа в списке (`{"kam": "КАМ-03", …}` в `activity.kams`, `overdue.kams`)."""
    alias = item.get("kam") if isinstance(item, Mapping) else None
    return alias if isinstance(alias, str) and CANONICAL_ALIAS_RE.fullmatch(alias) else None


def _walk(value: Any, allowed: _Allowed, rows: dict[str, _Allowed]) -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, int | float):
        allowed.add_number(value)
    elif isinstance(value, str):
        _absorb_text(value, allowed)
    elif isinstance(value, Mapping):
        for item in value.values():
            _walk(item, allowed, rows)
    elif isinstance(value, list | tuple):
        allowed.add_number(len(value))
        for item in value:
            alias = _row_alias(item)
            if alias is None:
                _walk(item, allowed, rows)
            else:
                _walk(item, rows.setdefault(alias, _Allowed()), rows)


def allowed_values(facts: Mapping[str, Any]) -> AllowedFacts:
    general = _Allowed()
    rows: dict[str, _Allowed] = {}
    _walk(facts, general, rows)
    return AllowedFacts(general=general, by_alias=rows, full=general.merged(*rows.values()))


# --------------------------------------------------------------------------------------
# Проверка фразы
# --------------------------------------------------------------------------------------


@dataclass
class _PhraseResult:
    text: str
    numbers: int
    reason: str | None = None  # None — фраза остаётся


def normalize_aliases(text: str) -> tuple[str, list[str]]:
    """`КАМ-1` / `КАМ‑01` / `КАМ 1:` → `КАМ-01`; возвращает текст и найденные псевдонимы."""
    found: list[str] = []

    def replace(match: re.Match[str]) -> str:
        alias = f"КАМ-{int(match.group(1) or match.group(2)):02d}"
        found.append(alias)
        return alias

    return ALIAS_RE.sub(replace, text), found


def _strip_known_quotes(text: str, texts: set[str]) -> str:
    def replace(match: re.Match[str]) -> str:
        inner = next((group for group in match.groups() if group is not None), "")
        inner = inner.strip().casefold()
        return " " if inner and inner in texts else match.group(0)

    return QUOTE_RE.sub(replace, text)


def _check_numbers(text: str, allowed: _Allowed) -> tuple[int, bool]:
    """(сколько чисел и дат во фразе, все ли совпали с фактами)."""
    text = CANONICAL_ALIAS_RE.sub(" ", text)
    text = WORKFLOW_RE.sub(" ", text)
    text = _strip_known_quotes(text, allowed.texts)
    count = 0
    ok = True
    for match in DATE_RE.finditer(text):
        count += 1
        day, month, year = match.groups()
        if _date_key(day, month, year) not in allowed.dates:
            ok = False
    text = DATE_RE.sub(" ", text)
    for match in NUMBER_RE.finditer(text):
        number = _parse_number(match.group(0))
        if number is None:
            continue
        count += 1
        if not allowed.matches(number):
            ok = False
    return count, ok


def check_phrase(
    phrase: str, *, section: str, aliases: Mapping[str, Any], allowed: AllowedFacts
) -> _PhraseResult:
    text, found = normalize_aliases(phrase.strip())
    scope = allowed.for_aliases(found) if found else allowed.full
    count, numbers_ok = _check_numbers(text, scope)
    # советы и оценки ищем без названий из фактов: этап «Ускорение найма» — не совет
    plain = _strip_known_quotes(text, allowed.texts)
    if any(alias not in aliases for alias in found):
        return _PhraseResult(text, count, "unknown_alias")
    if LINK_RE.search(text):
        return _PhraseResult(text, count, "link")
    if NO_ADVICE_RE.search(plain) or SUMMARY_ADVICE_RE.search(plain):
        return _PhraseResult(text, count, "advice")
    if EVALUATIVE_RE.search(plain):
        return _PhraseResult(text, count, "evaluative")
    if section == "questions" and not text.rstrip().endswith("?"):
        return _PhraseResult(text, count, "not_question")
    if not numbers_ok:
        return _PhraseResult(text, count, "numbers")
    if found and section != "questions" and count == 0:
        return _PhraseResult(text, count, "person_without_numbers")
    if len(text) > PHRASE_MAX:
        return _PhraseResult(text, count, "too_long")
    return _PhraseResult(text, count)


def split_sentences(text: str) -> list[str]:
    return [part.strip() for part in SENTENCE_SPLIT_RE.split(text.strip()) if part.strip()]


def _items(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        return [value]
    return [item for item in value or () if isinstance(item, str)]


def verify(
    sections: Mapping[str, Any],
    facts: Mapping[str, Any],
    aliases: Mapping[str, Any],
    *,
    enforce: bool = True,
) -> Verified:
    """Проверяет разделы сводки; `enforce=False` (сводка по правилам) — без порогов провала."""
    allowed = allowed_values(facts)
    numbers_total = numbers_matched = removed = advice = kept = 0
    by_reason = dict.fromkeys(REMOVED_GROUPS, 0)

    def run(phrase: str, section: str) -> str | None:
        nonlocal numbers_total, numbers_matched, removed, advice, kept
        if not phrase.strip():
            return None
        result = check_phrase(phrase, section=section, aliases=aliases, allowed=allowed)
        numbers_total += result.numbers
        if result.reason is None:
            numbers_matched += result.numbers
            kept += 1
            return result.text
        removed += 1
        by_reason[REASON_GROUPS.get(result.reason, "other")] += 1
        if result.reason == "advice":
            advice += 1
        return None

    headline_parts = [
        text
        for text in (
            run(sentence, "headline")
            for sentence in split_sentences(str(sections.get("headline") or ""))
        )
        if text is not None
    ]
    result: dict[str, Any] = {"headline": " ".join(headline_parts)}
    for section in LIST_SECTIONS:
        kept_items = [run(item, section) for item in _items(sections.get(section))]
        result[section] = [item for item in kept_items if item is not None]
    checks = {
        "numbers_total": numbers_total,
        "numbers_matched": numbers_matched,
        "sentences_removed": removed,
        "advice_removed": advice,
        "removed_by_reason": by_reason,
    }
    total = kept + removed
    if enforce:
        if not result["headline"]:
            raise VerificationFailed("headline_removed", checks)
        if total and removed * 2 > total:
            raise VerificationFailed("too_many_removed", checks)
        # короткий ответ без удалений (пустой период) — не провал; после удалений — провал
        if removed and kept < 2:
            raise VerificationFailed("too_few_phrases", checks)
    return Verified(sections=result, checks=checks)


def mentioned_aliases(sections: Mapping[str, Any]) -> list[str]:
    """Псевдонимы в порядке первого упоминания (заголовок, затем разделы по порядку)."""
    seen: list[str] = []
    texts = [str(sections.get("headline") or "")]
    for section in LIST_SECTIONS:
        texts.extend(_items(sections.get(section)))
    for text in texts:
        for alias in CANONICAL_ALIAS_RE.findall(text):
            if alias not in seen:
                seen.append(alias)
    return seen
