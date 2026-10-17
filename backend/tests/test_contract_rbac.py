"""Контракт каталога прав: фронт (`shared/auth/permissions.ts`) против `core/rbac_catalog.py`.

Права определяет только код бэкенда; фронт держит зеркало кодов для типизации проверок
и запасного поведения без блока `access` в `/auth/me`. Зеркало пишется руками, поэтому
сверяется здесь, без сборки фронта — разбором исходника:
- коды прав в объекте `P` — ровно каталог бэкенда, без повторов; имя ключа выводится
  из кода (`requests:write_any` → `REQUESTS_WRITE_ANY`);
- группы: комментарий `// <Подпись группы>` в `P` открывает группу, право стоит в своей
  группе каталога, порядок групп — как в `PERMISSION_GROUPS`;
- запасной список прав с областью `SCOPED_PERMISSIONS` и набор прав конструктора
  `WORKFLOW_PERMISSION_CODES` совпадают с каталогом;
- процессы, их подписи, области записей и формат кода роли — те же, что на бэкенде.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.rbac_catalog import (
    PERMISSION_GROUPS,
    PERMISSIONS,
    PERMISSIONS_BY_CODE,
    RECORD_SCOPES,
    ROLE_CODE_PATTERN,
    SCOPED_PERMISSIONS,
    WORKFLOW_TYPE_LABELS,
    WORKFLOW_TYPES,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"
PERMISSIONS_TS = FRONTEND_SRC / "shared" / "auth" / "permissions.ts"
TYPES_TS = FRONTEND_SRC / "shared" / "api" / "types.ts"
ROLE_MODEL_TS = FRONTEND_SRC / "pages" / "admin" / "roles" / "roleModel.ts"

_P_BLOCK = re.compile(r"^export const P = \{\n(.*?)^\} as const;", re.M | re.S)
_GROUP_COMMENT = re.compile(r"^\s*//\s*(.+?)\s*$")
_ENTRY = re.compile(r"^\s*([A-Z][A-Z0-9_]*):\s*'([^']+)',\s*$")
_GROUP_SUFFIX = re.compile(r"\s*\(.*\)$")  # «Заявки (действуют в процессах роли)» → «Заявки»


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _const_block(source: str, name: str) -> str:
    match = re.search(rf"^export const {name}\b[^=]*=\s*(.*?);\s*$", source, re.M | re.S)
    assert match, f"permissions.ts: не найдена константа {name}"
    return match.group(1)


@pytest.fixture(scope="module")
def ts_source() -> str:
    return _source(PERMISSIONS_TS)


@pytest.fixture(scope="module")
def ts_permissions(ts_source: str) -> list[tuple[str, str, str | None]]:
    """Записи объекта `P`: (ключ, код, подпись группы из ближайшего `//`-комментария)."""
    block = _P_BLOCK.search(ts_source)
    assert block, "permissions.ts: не найден объект `export const P = { … } as const`"
    entries: list[tuple[str, str, str | None]] = []
    group: str | None = None
    for line in block.group(1).splitlines():
        if comment := _GROUP_COMMENT.match(line):
            group = _GROUP_SUFFIX.sub("", comment.group(1))
        elif entry := _ENTRY.match(line):
            entries.append((entry.group(1), entry.group(2), group))
    assert entries, "permissions.ts: в объекте P не разобрано ни одного права"
    return entries


def _p_refs(block: str) -> list[str]:
    """Коды прав по ссылкам `P.KEY` внутри константы."""
    return re.findall(r"\bP\.([A-Z][A-Z0-9_]*)\b", block)


# --- коды и группы -------------------------------------------------------------------------


def test_permission_codes_match_backend_catalog(ts_permissions) -> None:  # noqa: ANN001
    codes = [code for _key, code, _group in ts_permissions]
    duplicates = sorted({c for c in codes if codes.count(c) > 1})
    assert not duplicates, f"permissions.ts: коды прав повторяются: {duplicates}"
    backend = {p.code for p in PERMISSIONS}
    assert not sorted(backend - set(codes)), (
        f"права каталога бэкенда, которых нет в P (permissions.ts): {sorted(backend - set(codes))}"
    )
    assert not sorted(set(codes) - backend), (
        "коды в P (permissions.ts), которых нет в rbac_catalog.PERMISSIONS: "
        f"{sorted(set(codes) - backend)}"
    )


def test_permission_keys_follow_codes(ts_permissions) -> None:  # noqa: ANN001
    wrong = [
        (key, code)
        for key, code, _group in ts_permissions
        if key != re.sub(r"[^A-Z0-9]", "_", code.upper())
    ]
    assert not wrong, f"ключ P не соответствует коду права (опечатка/копипаст?): {wrong}"


def test_permission_groups_match_backend_catalog(ts_permissions) -> None:  # noqa: ANN001
    labels = dict(PERMISSION_GROUPS)
    misplaced = [
        (code, group, labels[PERMISSIONS_BY_CODE[code].group])
        for _key, code, group in ts_permissions
        if code in PERMISSIONS_BY_CODE and group != labels[PERMISSIONS_BY_CODE[code].group]
    ]
    assert not misplaced, (
        "право в P стоит не под комментарием своей группы каталога "
        f"(код, группа во фронте, группа на бэкенде): {misplaced}"
    )
    ts_order = list(dict.fromkeys(group for _key, _code, group in ts_permissions))
    assert ts_order == [label for _code, label in PERMISSION_GROUPS]


# --- производные списки --------------------------------------------------------------------


def test_scoped_permissions_match_backend(ts_source: str, ts_permissions) -> None:  # noqa: ANN001
    by_key = {key: code for key, code, _group in ts_permissions}
    refs = _p_refs(_const_block(ts_source, "SCOPED_PERMISSIONS"))
    unknown = sorted(set(refs) - set(by_key))
    assert not unknown, f"SCOPED_PERMISSIONS ссылается на неизвестные ключи P: {unknown}"
    assert {by_key[r] for r in refs} == set(SCOPED_PERMISSIONS)


def test_workflow_permission_codes_match_backend(ts_source: str, ts_permissions) -> None:  # noqa: ANN001
    by_key = {key: code for key, code, _group in ts_permissions}
    refs = _p_refs(_const_block(ts_source, "WORKFLOW_PERMISSION_CODES"))
    assert {by_key[r] for r in refs} == {p.code for p in PERMISSIONS if p.group == "workflow"}


def test_write_permissions_reference_known_codes(ts_source: str, ts_permissions) -> None:  # noqa: ANN001
    keys = {key for key, _code, _group in ts_permissions}
    refs = _p_refs(_const_block(ts_source, "WRITE_PERMISSIONS"))
    assert refs and set(refs) <= keys
    by_key = {key: code for key, code, _group in ts_permissions}
    viewing = sorted(by_key[r] for r in refs if by_key[r].endswith(":read"))
    assert not viewing, f"право просмотра попало в WRITE_PERMISSIONS: {viewing}"


# --- процессы, области, формат кода роли ---------------------------------------------------


def test_workflow_types_and_labels_match_backend(ts_source: str) -> None:
    types_block = _const_block(ts_source, "WORKFLOW_TYPES")
    assert tuple(re.findall(r"'([^']+)'", types_block)) == WORKFLOW_TYPES
    labels_block = _const_block(ts_source, "WORKFLOW_TYPE_LABELS")
    assert dict(re.findall(r"(\w+):\s*'([^']+)'", labels_block)) == WORKFLOW_TYPE_LABELS
    union = re.search(r"^export type WorkflowType = (.+);$", _source(TYPES_TS), re.M)
    assert union, "types.ts: не найден тип WorkflowType"
    assert set(re.findall(r"'([^']+)'", union.group(1))) == set(WORKFLOW_TYPES)


def test_record_scopes_match_backend() -> None:
    union = re.search(r"^export type RecordScope = (.+);$", _source(TYPES_TS), re.M)
    assert union, "types.ts: не найден тип RecordScope"
    assert set(re.findall(r"'([^']+)'", union.group(1))) == set(RECORD_SCOPES)


def test_role_code_pattern_matches_backend() -> None:
    match = re.search(r"^export const ROLE_CODE_RE = /(.+)/;$", _source(ROLE_MODEL_TS), re.M)
    assert match, "roleModel.ts: не найдена константа ROLE_CODE_RE"
    assert match.group(1) == ROLE_CODE_PATTERN
