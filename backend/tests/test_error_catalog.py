"""Каталог кодов ошибок: таблица api-contract.md §1.4 = ERROR_CODES = коды, которые
реально возвращает сервер (НФТ3, находка критики P1-4).

Коды в коде ищутся по AST всего `app/`: литералы `ApiError(<статус>, "<код>")` и
`_error_response(<статус>, "<код>")` (ответы middleware), вызовы обёрток, которые
передают код параметром (`_conflict("role_exists", …)` → 409), и таблица кодов для
«голых» HTTPException фреймворка (`_STATUS_CODES`).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import (
    _STATUS_CODES,
    ERROR_CODES,
    ApiError,
    ErrorEnvelope,
    RequestIdMiddleware,
    error_payload,
    register_error_handlers,
    status_error_code,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = REPO_ROOT / "backend" / "app"
CONTRACT = REPO_ROOT / "docs" / "design" / "api-contract.md"

# функции, которые строят тело ошибки: имя → (индекс статуса, индекс кода)
_EMITTERS = {"ApiError": (0, 1), "_error_response": (0, 1)}
_KEYWORDS = {"ApiError": ("status_code", "code"), "_error_response": ("status_code", "code")}


# --- разбор таблицы §1.4 -----------------------------------------------------------------


def contract_codes() -> dict[str, int]:
    """Коды таблицы §1.4: строки `| <статус> | `код`[, `код`…] | … |` между §1.4 и §1.5."""
    text = CONTRACT.read_text(encoding="utf-8")
    start = text.index("### 1.4.")
    section = text[start : text.index("### 1.5.", start)]
    codes: dict[str, int] = {}
    for line in section.splitlines():
        match = re.match(r"^\|\s*(\d{3})\s*\|([^|]*)\|", line)
        if not match:
            continue
        for code in re.findall(r"`([a-z_]+)`", match.group(2)):
            assert code not in codes, f"код `{code}` дважды в таблице §1.4"
            codes[code] = int(match.group(1))
    return codes


# --- поиск кодов в коде ------------------------------------------------------------------


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _argument(call: ast.Call, index: int, keyword: str | None) -> ast.AST | None:
    if len(call.args) > index:
        return call.args[index]
    for item in call.keywords:
        if item.arg == keyword:
            return item.value
    return None


def _emitter_args(call: ast.Call) -> tuple[ast.AST | None, ast.AST | None] | None:
    name = _call_name(call.func)
    if name not in _EMITTERS:
        return None
    status_index, code_index = _EMITTERS[name]
    status_kw, code_kw = _KEYWORDS[name]
    return _argument(call, status_index, status_kw), _argument(call, code_index, code_kw)


def _int(node: ast.AST | None) -> int | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, int) else None


def _str(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def emitted_codes(root: Path = APP_DIR) -> dict[tuple[int, str], set[str]]:
    """(статус, код) → файлы, где сервер возвращает этот код."""
    trees = {path: ast.parse(path.read_text(encoding="utf-8")) for path in root.rglob("*.py")}

    # обёртки: функция передаёт свой параметр кодом в ApiError(<литерал-статус>, <параметр>)
    wrappers: dict[str, tuple[int, int]] = {}
    for tree in trees.values():
        for func in ast.walk(tree):
            if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            params = [arg.arg for arg in func.args.args]
            for call in ast.walk(func):
                args = _emitter_args(call) if isinstance(call, ast.Call) else None
                if args is None:
                    continue
                status, code = _int(args[0]), args[1]
                if status is not None and isinstance(code, ast.Name) and code.id in params:
                    wrappers[func.name] = (params.index(code.id), status)

    found: dict[tuple[int, str], set[str]] = {}
    for path, tree in trees.items():
        where = str(path.relative_to(root.parent))
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            args = _emitter_args(call)
            if args is not None:
                status, code = _int(args[0]), _str(args[1])
                if status is not None and code is not None:
                    found.setdefault((status, code), set()).add(where)
                continue
            name = _call_name(call.func)
            if name in wrappers:
                index, status_value = wrappers[name]
                code = _str(_argument(call, index, None))
                if code is not None:
                    found.setdefault((status_value, code), set()).add(where)
    for status_value, code in _STATUS_CODES.items():
        found.setdefault((status_value, code), set()).add("app/core/errors.py:_STATUS_CODES")
    return found


@pytest.fixture(scope="module")
def emitted() -> dict[tuple[int, str], set[str]]:
    return emitted_codes()


# --- сверка ------------------------------------------------------------------------------


def test_contract_table_matches_catalog() -> None:
    table = contract_codes()
    catalog = {code: status for code, (status, _) in ERROR_CODES.items()}
    assert set(table) - set(catalog) == set(), "в §1.4 есть коды, которых нет в ERROR_CODES"
    assert set(catalog) - set(table) == set(), "в ERROR_CODES есть коды, которых нет в §1.4"
    mismatched = {code: (table[code], status) for code, status in catalog.items()}
    assert {c: s for c, s in mismatched.items() if s[0] != s[1]} == {}, "статусы §1.4 и ERROR_CODES"


def test_every_catalog_code_is_returned_by_server(
    emitted: dict[tuple[int, str], set[str]],
) -> None:
    pairs = set(emitted)
    missing = {
        code: status for code, (status, _) in ERROR_CODES.items() if (status, code) not in pairs
    }
    assert missing == {}, "коды каталога, которых сервер не возвращает: убрать из §1.4"


def test_every_returned_code_is_in_catalog(emitted: dict[tuple[int, str], set[str]]) -> None:
    unknown = {
        f"{status} {code}": sorted(files)
        for (status, code), files in emitted.items()
        if ERROR_CODES.get(code, (None,))[0] != status
    }
    assert unknown == {}, "коды из кода, которых нет в каталоге (или с другим статусом)"


def test_removed_codes_stay_removed(emitted: dict[tuple[int, str], set[str]]) -> None:
    # критика P1-4: коды без реализации (ограничителя частоты, заглушек 501, старых
    # имён идемпотентности) не должны вернуться ни в таблицу, ни в код
    table = contract_codes()
    codes = {code for _, code in emitted}
    for code in (
        "rate_limited",
        "not_implemented",
        "http_error",
        "idempotency_conflict",
        "workflow_change_requires_approval",
    ):
        assert code not in table and code not in ERROR_CODES and code not in codes, code


def test_scanner_sees_wrappers_and_middleware(emitted: dict[tuple[int, str], set[str]]) -> None:
    # страховка самого сканера: обёртка `_conflict(code, …)`, ответы middleware без ApiError
    assert any("access/service.py" in f for f in emitted[(409, "role_exists")])
    assert any("idempotency.py" in f for f in emitted[(422, "idempotency_key_reused")])
    assert any("workflow/locks.py" in f for f in emitted[(409, "workflow_busy")])
    assert (405, "method_not_allowed") in emitted


def test_scanner_on_synthetic_module(tmp_path: Path) -> None:
    package = tmp_path / "app"
    package.mkdir()
    (package / "mod.py").write_text(
        "def _gone(code, message):\n"
        "    return ApiError(410, code, message)\n"
        "def a():\n"
        "    raise ApiError(status_code=418, code='teapot', message='x')\n"
        "def b():\n"
        "    raise _gone('gone_forever', 'x')\n"
        "def c():\n"
        "    return _error_response(429, 'slow_down', 'x')\n",
        encoding="utf-8",
    )
    found = emitted_codes(package)
    assert {(418, "teapot"), (410, "gone_forever"), (429, "slow_down")} <= set(found)


# --- поведение обработчиков в рамках каталога ---------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)

    @app.get("/only-get")
    async def only_get() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/teapot")
    async def teapot() -> None:
        raise StarletteHTTPException(status_code=418)

    @app.get("/gateway-timeout")
    async def gateway_timeout() -> None:
        raise StarletteHTTPException(status_code=504, detail="Шлюз не ответил")

    @app.get("/domain")
    async def domain() -> None:
        raise ApiError(
            400,
            "validation_error",
            "Ошибка валидации данных",
            details=[{"field": "permissions", "code": "x", "message": "m", "requires": ["a"]}],
        )

    return TestClient(app, raise_server_exceptions=False)


def test_method_not_allowed_is_catalog_code(client: TestClient) -> None:
    response = client.post("/only-get")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


def test_unmapped_statuses_fall_back_to_catalog_codes(client: TestClient) -> None:
    teapot = client.get("/teapot")
    assert teapot.status_code == 418
    assert teapot.json()["error"]["code"] == "validation_error"
    timeout = client.get("/gateway-timeout")
    assert timeout.status_code == 504
    assert timeout.json()["error"] == {
        "code": "internal_error",
        "message": "Шлюз не ответил",
        "trace_id": timeout.headers["X-Request-Id"],
    }
    assert status_error_code(404) == "not_found"
    assert set(_STATUS_CODES.values()) <= set(ERROR_CODES)


def test_error_envelope_model_describes_real_body(client: TestClient) -> None:
    # модель Swagger принимает то, что реально отдаёт сервер (включая лишние ключи details)
    body = client.get("/domain").json()
    parsed = ErrorEnvelope.model_validate(body)
    assert parsed.error.code == "validation_error"
    assert parsed.error.details and parsed.error.details[0].field == "permissions"
    assert ErrorEnvelope.model_validate(error_payload("not_found", "Объект не найден"))
    required = ErrorEnvelope.model_json_schema(mode="serialization")["$defs"]["ErrorBody"]
    assert set(required["required"]) == {"code", "message", "trace_id"}
