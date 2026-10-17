"""Перечень библиотек и компонентов «Альмы» → `docs/architecture/libraries.md`.

Источники — только файлы репозитория и локальные метаданные, без сети:

- Python: `uv.lock` backend и трёх сервисов (`services/*`). По ним образы собирает
  `uv sync --frozen --no-dev`, поэтому версии берутся отсюда. Корень lock-файла даёт прямые
  зависимости и группу `dev`; маркеры вычисляются для целевой платформы образов
  (Linux x86_64, CPython 3.12);
- лицензии Python-пакетов — из метаданных установленных дистрибутивов: окружение, в котором
  запущен скрипт, `.venv` сервисов (если есть) и пути `--site-packages`. Пакеты, которых нет
  локально, берут лицензию из справочной таблицы `KNOWN_PY_LICENSES` и помечаются «¹»;
- frontend: `package-lock.json` (версия, лицензия, признаки dev и optional) и
  `package.json` (прямые зависимости);
- образы: `FROM` в Dockerfile, `image:` в `deploy/**/*.yaml|yml` и workflow CI, образ k6 из
  инструкции нагрузочного теста; действия GitHub Actions — `uses:` в `.github/workflows`.
  Лицензии образов и действий — справочно, по основному ПО образа («¹»);
- сверка `backend/requirements.txt` и `requirements-dev.txt` с `uv.lock`.

Запуск из `backend/`:

    .venv/bin/python -m app.tools.list_libraries          # перезаписать libraries.md
    .venv/bin/python -m app.tools.list_libraries --check  # exit 1, если файл устарел
"""

from __future__ import annotations

import argparse
import importlib.metadata as md
import json
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10: tomli есть в dev-зависимостях
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parents[3]
OUTPUT = REPO_ROOT / "docs" / "architecture" / "libraries.md"
COMMAND = "cd backend && .venv/bin/python -m app.tools.list_libraries"

# (имя в перечне, каталог проекта с uv.lock)
PY_PROJECTS: tuple[tuple[str, str], ...] = (
    ("backend", "backend"),
    ("notification", "services/notification"),
    ("lms-stub", "services/lms-stub"),
    ("cms-stub", "services/cms-stub"),
    ("llm-gateway", "services/llm-gateway"),
)

# целевая платформа образов: python:3.12-slim-bookworm, linux/amd64
TARGET_ENV: dict[str, str] = {
    "python_version": "3.12",
    "python_full_version": "3.12.0",
    "implementation_name": "cpython",
    "implementation_version": "3.12.0",
    "platform_python_implementation": "CPython",
    "platform_machine": "x86_64",
    "platform_system": "Linux",
    "platform_release": "",
    "platform_version": "",
    "sys_platform": "linux",
    "os_name": "posix",
    "extra": "",
}
TARGET_LABEL = "Linux x86_64, CPython 3.12"

KIND_DIRECT = "прямая"
KIND_TRANSITIVE = "транзитивная"
KIND_DEV = "разработка"
_KIND_RANK = {KIND_DIRECT: 0, KIND_TRANSITIVE: 1, KIND_DEV: 2}

KNOWN_MARK = "¹"

# лицензии Python-пакетов, которых может не быть в локальном окружении (зависимости сервисов
# и пакеты только для старых Python/Windows) — по метаданным релизов на PyPI
KNOWN_PY_LICENSES: dict[str, str] = {
    "aiofiles": "Apache-2.0",
    "aiogram": "MIT",
    "aiohappyeyeballs": "PSF-2.0",
    "aiohttp": "Apache-2.0",
    "aiosignal": "Apache-2.0",
    "async-timeout": "Apache-2.0",
    "attrs": "MIT",
    "backports-asyncio-runner": "PSF-2.0",
    "colorama": "BSD-3-Clause",
    "frozenlist": "Apache-2.0",
    "jinja2": "BSD-3-Clause",
    "magic-filter": "MIT",
    "multidict": "Apache-2.0",
    "propcache": "Apache-2.0",
    "yarl": "Apache-2.0",
}

# короткие формы поля License и классификаторов → SPDX
LICENSE_ALIASES: dict[str, str] = {
    "MIT License": "MIT",
    "PSFL": "PSF-2.0",
    "Apache 2.0": "Apache-2.0",
    "Apache License 2.0": "Apache-2.0",
    "Apache Software License": "Apache-2.0",
    "BSD License": "BSD",
    "MPL 2.0": "MPL-2.0",
}
CLASSIFIER_LICENSES: dict[str, str] = {
    "MIT License": "MIT",
    "BSD License": "BSD",
    "Apache Software License": "Apache-2.0",
    "Python Software Foundation License": "PSF-2.0",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "ISC License (ISCL)": "ISC",
    "GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0",
    "GNU General Public License v3 (GPLv3)": "GPL-3.0",
}

# назначение ключевых пакетов
PY_PURPOSES: dict[str, str] = {
    "fastapi": "веб-фреймворк REST API и Swagger UI",
    "starlette": "ASGI-основа FastAPI: маршрутизация, middleware",
    "uvicorn": "ASGI-сервер",
    "uvloop": "быстрый цикл событий для uvicorn",
    "httptools": "разбор HTTP для uvicorn",
    "websockets": "протокол WebSocket для uvicorn[standard]",
    "watchfiles": "перезапуск uvicorn при разработке",
    "python-dotenv": "чтение .env в uvicorn",
    "pyyaml": "конфигурация логирования uvicorn",
    "sqlalchemy": "ORM и построитель SQL (async)",
    "greenlet": "асинхронный режим SQLAlchemy",
    "asyncpg": "асинхронный драйвер PostgreSQL",
    "alembic": "миграции схемы БД",
    "mako": "шаблоны миграций Alembic",
    "pydantic": "схемы запросов и ответов, валидация",
    "pydantic-core": "ядро валидации Pydantic",
    "pydantic-settings": "настройки из переменных окружения",
    "pyjwt": "проверка JWT Keycloak",
    "cryptography": "шифрование ПДн (Fernet), подписи JWT",
    "redis": "клиент KeyDB: кэш, pub/sub, идемпотентность",
    "minio": "клиент S3-хранилища MinIO",
    "argon2-cffi": "зависимость клиента MinIO",
    "pycryptodome": "зависимость клиента MinIO",
    "httpx": "HTTP-клиент: интеграции, Keycloak, notification-service",
    "httpcore": "транспорт httpx",
    "openpyxl": "чтение и запись XLSX",
    "et-xmlfile": "потоковая запись XML для openpyxl",
    "xlrd": "чтение старого XLS (BIFF)",
    "charset-normalizer": "определение кодировки импортируемых файлов",
    "sse-starlette": "Server-Sent Events",
    "orjson": "быстрая сериализация JSON",
    "python-multipart": "загрузка файлов (multipart/form-data)",
    "fpdf2": "PDF отчётов с кириллицей",
    "fonttools": "встраивание шрифтов в PDF (fpdf2)",
    "pillow": "изображения в PDF (fpdf2)",
    "defusedxml": "безопасный разбор XML (fpdf2)",
    "aiogram": "Telegram-бот (long-polling)",
    "aiohttp": "HTTP-клиент aiogram",
    "jinja2": "шаблоны текстов уведомлений",
    "markupsafe": "экранирование в шаблонах",
    "certifi": "корневые сертификаты TLS",
    "anyio": "асинхронный ввод-вывод для Starlette и httpx",
    "pytest": "тесты",
    "pytest-asyncio": "асинхронные тесты",
    "ruff": "линтер и форматтер",
}
NPM_PURPOSES: dict[str, str] = {
    "react": "библиотека интерфейса",
    "react-dom": "отрисовка React в браузере",
    "react-router-dom": "маршрутизация экранов",
    "@tanstack/react-query": "запросы к API и кэш данных",
    "@tanstack/react-table": "таблицы-реестры с колонками и сортировкой",
    "@xyflow/react": "визуальный конструктор процессов (граф этапов)",
    "echarts": "графики аналитических панелей",
    "echarts-for-react": "обёртка ECharts для React",
    "keycloak-js": "вход через Keycloak (OIDC + PKCE)",
    "tailwindcss": "CSS-утилиты и токены оформления",
    "@tailwindcss/vite": "подключение Tailwind к сборке Vite",
    "tw-animate-css": "анимации для Tailwind",
    "class-variance-authority": "варианты компонентов (shadcn/ui)",
    "clsx": "сборка CSS-классов",
    "tailwind-merge": "слияние классов Tailwind",
    "cmdk": "командная палитра и поиск",
    "dayjs": "даты и периоды",
    "lucide-react": "иконки",
    "motion": "анимации интерфейса",
    "qrcode": "QR-код привязки Telegram",
    "react-day-picker": "выбор дат и периодов",
    "react-hook-form": "формы",
    "@hookform/resolvers": "валидация форм схемами zod",
    "zod": "схемы валидации данных",
    "zustand": "состояние интерфейса",
    "sonner": "всплывающие уведомления",
    "@dnd-kit/core": "перетаскивание карточек доски",
    "@dnd-kit/sortable": "сортировка перетаскиванием",
    "@dnd-kit/utilities": "утилиты dnd-kit",
    "@fontsource-variable/inter": "шрифт Inter без CDN",
    "typescript": "проверка типов",
    "vite": "сборка и dev-сервер",
    "@vitejs/plugin-react": "React в сборке Vite",
    "@types/react": "типы React",
    "@types/react-dom": "типы React DOM",
    "@types/qrcode": "типы qrcode",
    "eslint": "проверка правил хуков React (npm run lint)",
    "eslint-plugin-react-hooks": "правила хуков React для ESLint",
    "@typescript-eslint/parser": "разбор TypeScript для ESLint",
}
NPM_PREFIX_PURPOSES: tuple[tuple[str, str], ...] = (
    ("@radix-ui/react-", "примитив интерфейса Radix (основа shadcn/ui)"),
)

# справочно: лицензия основного ПО образа и назначение
IMAGE_INFO: dict[str, tuple[str, str]] = {
    "postgres": ("PostgreSQL", "СУБД CRM и Keycloak; psql и pg_isready в init-контейнерах"),
    "quay.io/keycloak/keycloak": ("Apache-2.0", "вход пользователей и сервисов (OIDC)"),
    "quay.io/minio/minio": ("AGPL-3.0", "S3-хранилище файлов"),
    "quay.io/minio/mc": ("AGPL-3.0", "создание бакетов MinIO (Job minio-init)"),
    "eqalpha/keydb": ("BSD-3-Clause", "кэш, pub/sub событий SSE, идемпотентность"),
    "curlimages/curl": ("curl", "вызов служебных эндпоинтов из CronJob и init-контейнеров"),
    "python": ("PSF-2.0", "среда выполнения Python-сервисов (runtime-стадия)"),
    "ghcr.io/astral-sh/uv": ("MIT OR Apache-2.0", "установка зависимостей по uv.lock"),
    "node": ("MIT", "сборка интерфейса (стадия build)"),
    "nginxinc/nginx-unprivileged": ("BSD-2-Clause", "раздача интерфейса без root"),
    "quay.io/jetstack/cert-manager-cainjector": ("Apache-2.0", "TLS-сертификаты стенда на ВМ"),
    "quay.io/jetstack/cert-manager-controller": ("Apache-2.0", "TLS-сертификаты стенда на ВМ"),
    "quay.io/jetstack/cert-manager-webhook": ("Apache-2.0", "TLS-сертификаты стенда на ВМ"),
    "grafana/k6": ("AGPL-3.0", "нагрузочное тестирование"),
}
ACTION_INFO: dict[str, tuple[str, str]] = {
    "actions/checkout": ("MIT", "получение кода в CI"),
    "actions/setup-node": ("MIT", "Node.js и кэш npm в CI"),
    "astral-sh/setup-uv": ("MIT", "uv и Python в CI"),
}
# лицензии, о которых стоит сказать отдельно (copyleft, слабый copyleft, шрифтовые)
NOTABLE_LICENSE_RE = re.compile(r"GPL|MPL|EPL|CDDL|OFL|CC-BY|Unlicense|^$")

_OWN_IMAGE_PREFIX = "crm/"
# суффикс платформенной сборки npm: lightningcss-linux-x64-gnu → lightningcss
_PLATFORM_SUFFIX_RE = re.compile(
    r"-(android|darwin|freebsd|linux|win32|wasm32|openharmony|netbsd|openbsd|sunos|aix)\b.*$"
)


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


# --- Python ----------------------------------------------------------------------------------


def _marker_ok(marker: str | None) -> bool:
    if not marker:
        return True
    try:
        from packaging.markers import Marker
    except ModuleNotFoundError:  # без packaging считаем условие выполненным
        return True
    return Marker(marker).evaluate(TARGET_ENV)


@dataclass
class PyEntry:
    name: str
    version: str
    uses: dict[str, str] = field(default_factory=dict)  # проект → вид зависимости
    excluded: dict[str, str] = field(default_factory=dict)  # проект → маркер

    @property
    def kind(self) -> str:
        return min(self.uses.values(), key=_KIND_RANK.__getitem__)


def read_uv_lock(project: str, path: Path) -> tuple[dict[tuple[str, str], PyEntry], str]:
    """Пакеты одного uv.lock: (имя, версия) → вид зависимости на целевой платформе."""
    lock = tomllib.loads(path.read_text(encoding="utf-8"))
    packages = lock["package"]
    by_name: dict[str, list[dict]] = {}
    for pkg in packages:
        by_name.setdefault(pkg["name"], []).append(pkg)
    roots = [p for p in packages if "registry" not in p.get("source", {})]
    if len(roots) != 1:
        raise ValueError(f"{path}: ожидался один корневой пакет, найдено {len(roots)}")
    root = roots[0]

    def resolve(dep: dict) -> dict:
        candidates = by_name[dep["name"]]
        if "version" in dep:
            return next(p for p in candidates if p["version"] == dep["version"])
        if len(candidates) != 1:
            raise ValueError(f"{path}: неоднозначная зависимость {dep['name']}")
        return candidates[0]

    entries: dict[tuple[str, str], PyEntry] = {}
    excluded: dict[tuple[str, str], str] = {}

    def walk(deps: Iterable[dict], kind: str) -> None:
        stack = [(dep, True) for dep in deps]
        seen: set[tuple[str, str, tuple[str, ...]]] = set()
        while stack:
            dep, direct = stack.pop()
            pkg = resolve(dep)
            key = (pkg["name"], pkg["version"])
            if not _marker_ok(dep.get("marker")):
                excluded.setdefault(key, dep["marker"])
                continue
            this_kind = kind if kind == KIND_DEV else (KIND_DIRECT if direct else KIND_TRANSITIVE)
            entry = entries.setdefault(key, PyEntry(*key))
            current = entry.uses.get(project)
            if current is None or _KIND_RANK[this_kind] < _KIND_RANK[current]:
                entry.uses[project] = this_kind
            extras = tuple(sorted(dep.get("extra", ())))
            mark = (*key, extras)
            if mark in seen:
                continue
            seen.add(mark)
            children = list(pkg.get("dependencies", ()))
            for extra in extras:
                children += pkg.get("optional-dependencies", {}).get(extra, ())
            stack += [(child, False) for child in children]

    walk(root.get("dependencies", ()), KIND_TRANSITIVE)
    dev = list(root.get("optional-dependencies", {}).get("dev", ()))
    dev += root.get("dev-dependencies", {}).get("dev", ())
    walk(dev, KIND_DEV)
    for key, marker in excluded.items():
        if key not in entries:
            entry = entries.setdefault(key, PyEntry(*key))
            entry.excluded[project] = marker
    return entries, root["name"]


def collect_python() -> tuple[list[PyEntry], dict[str, str]]:
    merged: dict[tuple[str, str], PyEntry] = {}
    roots: dict[str, str] = {}
    for project, directory in PY_PROJECTS:
        entries, root_name = read_uv_lock(project, REPO_ROOT / directory / "uv.lock")
        roots[project] = root_name
        for key, entry in entries.items():
            target = merged.setdefault(key, PyEntry(*key))
            target.uses.update(entry.uses)
            target.excluded.update(entry.excluded)
    return sorted(merged.values(), key=lambda e: (canonical(e.name), e.version)), roots


def license_from_metadata(meta: md.PackageMetadata) -> str:
    expression = (meta.get("License-Expression") or "").strip()
    if expression:
        return expression
    raw = (meta.get("License") or "").strip()
    if raw and "\n" not in raw and len(raw) <= 40 and raw.upper() != "UNKNOWN":
        return LICENSE_ALIASES.get(raw, raw)
    found = []
    for classifier in meta.get_all("Classifier") or ():
        prefix = "License :: OSI Approved :: "
        if classifier.startswith(prefix):
            name = classifier.removeprefix(prefix)
            found.append(CLASSIFIER_LICENSES.get(name, name))
    return " / ".join(dict.fromkeys(found))  # несколько классификаторов — без трактовки SPDX


def installed_licenses(extra_paths: Iterable[Path]) -> dict[str, str]:
    """Лицензии установленных дистрибутивов: текущее окружение, .venv сервисов, --site-packages."""
    paths = [*sys.path]
    for _project, directory in PY_PROJECTS:
        paths += [str(p) for p in (REPO_ROOT / directory).glob(".venv/lib/python*/site-packages")]
    paths += [str(p) for p in extra_paths]
    result: dict[str, str] = {}
    for dist in md.distributions(path=paths):
        name = dist.metadata["Name"]
        if not name:
            continue
        license_ = license_from_metadata(dist.metadata)
        if license_:
            result.setdefault(canonical(name), license_)
    return result


def py_license(name: str, installed: dict[str, str]) -> str:
    key = canonical(name)
    if key in installed:
        return installed[key]
    if key in KNOWN_PY_LICENSES:
        return KNOWN_PY_LICENSES[key] + KNOWN_MARK
    return ""


# --- npm -------------------------------------------------------------------------------------


@dataclass
class NpmEntry:
    name: str
    version: str
    license: str
    direct: str  # "dependencies" | "devDependencies" | ""
    dev: bool
    platform: str  # «os/cpu» для платформенных сборок, иначе пусто


def collect_npm() -> list[NpmEntry]:
    frontend = REPO_ROOT / "frontend"
    lock = json.loads((frontend / "package-lock.json").read_text(encoding="utf-8"))
    manifest = json.loads((frontend / "package.json").read_text(encoding="utf-8"))
    runtime = set(manifest.get("dependencies", {}))
    dev = set(manifest.get("devDependencies", {}))
    result = []
    for key, info in lock.get("packages", {}).items():
        if not key or info.get("link"):
            continue
        name = info.get("name") or key.rsplit("node_modules/", 1)[-1]
        top_level = key == f"node_modules/{name}"
        direct = ""
        if top_level and name in runtime:
            direct = "dependencies"
        elif top_level and name in dev:
            direct = "devDependencies"
        license_ = info.get("license", "")
        if isinstance(license_, dict):
            license_ = license_.get("type", "")
        if not license_:
            license_ = _license_from_node_modules(frontend / key)
        platform = ""
        if info.get("optional") and (info.get("os") or info.get("cpu")):
            platform = "/".join(
                ",".join(info.get(part, ())) for part in ("os", "cpu") if info.get(part)
            )
        result.append(
            NpmEntry(
                name,
                info.get("version", ""),
                license_,
                direct,
                bool(info.get("dev") or info.get("devOptional")),
                platform,
            )
        )
    return sorted(result, key=lambda e: (e.name, e.version))


def _license_from_node_modules(package_dir: Path) -> str:
    try:
        data = json.loads((package_dir / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    license_ = data.get("license", "")
    return license_.get("type", "") if isinstance(license_, dict) else str(license_)


def npm_purpose(name: str) -> str:
    if name in NPM_PURPOSES:
        return NPM_PURPOSES[name]
    return next((text for prefix, text in NPM_PREFIX_PURPOSES if name.startswith(prefix)), "")


# --- образы и CI -----------------------------------------------------------------------------

_IMAGE_LINE_RE = re.compile(r"^\s*(?:-\s*)?image:\s*(?P<value>.+?)\s*$")
_IMAGE_REF_RE = re.compile(r"[a-z0-9][a-z0-9._/-]*(?::[\w.-]+|@sha256:[0-9a-f]{64})")
_FROM_RE = re.compile(r"^FROM\s+(?:--platform=\S+\s+)?(?P<ref>\S+)", re.I)
_USES_RE = re.compile(r"^\s*-?\s*uses:\s*(?P<ref>[\w./-]+@[\w.-]+)")
_DOCKER_RUN_IMAGE_RE = re.compile(r"^\s*(?P<ref>[a-z0-9][\w./-]*:[\w.-]+)\s+run\b", re.M)


@dataclass
class ImageUse:
    ref: str
    version_note: str = ""
    sources: set[str] = field(default_factory=set)


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def collect_images() -> tuple[dict[str, ImageUse], dict[str, list[str]]]:
    """Сторонние образы (ссылка → файлы) и собственные (образ → базовые образы Dockerfile)."""
    images: dict[str, ImageUse] = {}
    own: dict[str, list[str]] = {}

    def add(ref: str, source: Path, note: str = "") -> None:
        if ref.startswith(_OWN_IMAGE_PREFIX):
            return
        use = images.setdefault(ref, ImageUse(ref))
        use.sources.add(_rel(source))
        use.version_note = use.version_note or note

    dockerfiles = [REPO_ROOT / "backend" / "Dockerfile", REPO_ROOT / "frontend" / "Dockerfile"]
    dockerfiles += sorted(REPO_ROOT.glob("services/*/Dockerfile"))
    for dockerfile in dockerfiles:
        if not dockerfile.exists():
            continue
        stages: set[str] = set()
        bases = []
        for line in dockerfile.read_text(encoding="utf-8").splitlines():
            match = _FROM_RE.match(line)
            if not match:
                continue
            ref = match.group("ref")
            parts = line.split()
            if len(parts) >= 4 and parts[-2].upper() == "AS":
                stages.add(parts[-1])
            if ref in stages:
                continue
            bases.append(ref)
            add(ref, dockerfile)
        own[f"crm/{_own_name(dockerfile)}"] = bases

    manifests = [*REPO_ROOT.glob("deploy/**/*.yaml"), *REPO_ROOT.glob("deploy/**/*.yml")]
    manifests += [*REPO_ROOT.glob(".github/workflows/*.yml")]
    for manifest in sorted(manifests):
        if _skip(manifest):
            continue
        for line in manifest.read_text(encoding="utf-8").splitlines():
            match = _IMAGE_LINE_RE.match(line)
            if not match:
                continue
            value, _, comment = match.group("value").partition(" #")
            note = comment.strip() if re.match(r"\s*v?\d", comment) else ""
            for ref in _IMAGE_REF_RE.findall(value.strip("'\"")):
                add(ref, manifest, note)

    load_readme = REPO_ROOT / "deploy" / "load" / "README.md"
    if load_readme.exists():
        for match in _DOCKER_RUN_IMAGE_RE.finditer(load_readme.read_text(encoding="utf-8")):
            add(match.group("ref"), load_readme)
    return images, own


def _own_name(dockerfile: Path) -> str:
    return dockerfile.parent.name


def _skip(path: Path) -> bool:
    parts = path.relative_to(REPO_ROOT).parts
    return any(p in {".git", ".claude"} or p.startswith((".venv", "node_modules")) for p in parts)


def split_image(ref: str) -> tuple[str, str]:
    if "@" in ref:
        repo, digest = ref.split("@", 1)
        return repo, digest
    repo, _, tag = ref.rpartition(":")
    return (repo, tag) if repo and "/" not in tag else (ref, "")


def collect_actions() -> dict[str, set[str]]:
    actions: dict[str, set[str]] = {}
    for workflow in sorted(REPO_ROOT.glob(".github/workflows/*.yml")):
        for line in workflow.read_text(encoding="utf-8").splitlines():
            match = _USES_RE.match(line)
            if match:
                actions.setdefault(match.group("ref"), set()).add(_rel(workflow))
    return actions


def collect_fonts() -> list[tuple[str, str, str, str]]:
    """Шрифты в репозитории: (файлы, версия, лицензия, назначение)."""
    fonts_dir = REPO_ROOT / "backend" / "app" / "modules" / "reporting" / "fonts"
    files = sorted(fonts_dir.glob("*.ttf"))
    if not files:
        return []
    license_ = (
        "Bitstream Vera Fonts License; изменения DejaVu — public domain"
        if (fonts_dir / "LICENSE_DEJAVU").exists()
        else ""
    )
    return [
        (
            ", ".join(f.name for f in files),
            _font_version(files[0]),
            license_,
            f"кириллица в PDF отчётов ({_rel(fonts_dir)})",
        )
    ]


def _font_version(path: Path) -> str:
    try:
        from fontTools.ttLib import TTFont
    except ModuleNotFoundError:
        return ""
    try:
        name = TTFont(path, lazy=True)["name"].getDebugName(5) or ""
    except Exception:  # noqa: BLE001 — версия шрифта не обязательна для перечня
        return ""
    match = re.search(r"\d+(?:\.\d+)+", name)
    return match.group(0) if match else ""


# --- сверка requirements.txt -----------------------------------------------------------------


def requirements_drift(python: list[PyEntry]) -> list[tuple[str, str, str, str]]:
    """Расхождения пинов backend/requirements*.txt с версиями uv.lock для целевой платформы.

    Пин версии, которую lock держит только для других платформ (ветка для Python < 3.11),
    расхождением не считается, если пакет на целевой платформе не ставится вовсе.
    """
    lock_versions: dict[str, set[str]] = {}
    other_versions: dict[str, set[str]] = {}
    for entry in python:
        if "backend" in entry.uses:
            lock_versions.setdefault(canonical(entry.name), set()).add(entry.version)
        elif "backend" in entry.excluded:
            other_versions.setdefault(canonical(entry.name), set()).add(entry.version)
    rows = []
    for file_name in ("requirements.txt", "requirements-dev.txt"):
        path = REPO_ROOT / "backend" / file_name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.match(r"^([A-Za-z0-9_.-]+)==(\S+)", line.strip())
            if not match:
                continue
            name, version = canonical(match.group(1)), match.group(2)
            locked = lock_versions.get(name)
            other = other_versions.get(name, set())
            if locked is None:
                if version not in other:
                    rows.append((f"backend/{file_name}", name, version, "нет в uv.lock"))
            elif version not in locked:
                note = ", ".join(sorted(locked))
                if version in other:
                    note += f" ({version} в uv.lock — ветка для других версий Python)"
                rows.append((f"backend/{file_name}", name, version, note))
    return rows


# --- вывод -----------------------------------------------------------------------------------


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _table(header: tuple[str, ...], rows: Iterable[tuple[str, ...]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(_cell(c) or "—" for c in row) + " |" for row in rows]
    return lines


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} {one}"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} {few}"
    return f"{n} {many}"


def _where(entry: PyEntry) -> str:
    order = [p for p, _ in PY_PROJECTS]
    parts = []
    for project in order:
        kind = entry.uses.get(project)
        if kind:
            parts.append(project if kind == entry.kind else f"{project} ({kind})")
    return ", ".join(parts)


def render(
    python: list[PyEntry],
    roots: dict[str, str],
    py_licenses: dict[str, str],
    npm: list[NpmEntry],
    images: dict[str, ImageUse],
    own_images: dict[str, list[str]],
    actions: dict[str, set[str]],
    fonts: list[tuple[str, str, str, str]],
    drift: list[tuple[str, str, str, str]],
) -> str:
    shipped_py = [e for e in python if e.uses and e.kind != KIND_DEV]
    dev_py = [e for e in python if e.uses and e.kind == KIND_DEV]
    excluded_py = [e for e in python if not e.uses]
    npm_platform = [e for e in npm if e.platform]
    npm_main = [e for e in npm if not e.platform]
    npm_runtime = [e for e in npm_main if not e.dev]
    npm_dev = [e for e in npm_main if e.dev]

    out = [
        "# Перечень библиотек и компонентов",
        "",
        "> Файл генерирует скрипт `backend/app/tools/list_libraries.py`, вручную не правится.",
        f"> Обновить: `{COMMAND}`; проверить актуальность — с флагом `--check`.",
        "",
        "Все сторонние библиотеки и компоненты «Альмы»: Python-пакеты backend и трёх "
        "сервисов, npm-пакеты интерфейса, образы контейнеров, шрифты и действия CI. "
        "Версии взяты из lock-файлов, по которым собираются образы (`uv sync --frozen`, "
        "`npm ci`), и из манифестов развёртывания.",
        "",
        "## Сводка",
        "",
    ]
    out += _table(
        ("Группа", "Всего", "Состав", "Источник"),
        [
            (
                "Python-пакеты",
                str(len(python)),
                f"в образах — {len(shipped_py)}, для разработки и тестов — {len(dev_py)}, "
                f"только для других версий Python и ОС — {len(excluded_py)}",
                ", ".join(f"`{d}/uv.lock`" for _, d in PY_PROJECTS),
            ),
            (
                "npm-пакеты",
                str(len(npm)),
                f"без признака `dev` — {len(npm_runtime)}, с признаком `dev` (только сборка) — "
                f"{len(npm_dev)}, платформенные бинарные сборки — {len(npm_platform)}",
                "`frontend/package-lock.json`",
            ),
            (
                "Образы контейнеров",
                str(len(images) + len(own_images)),
                f"собственные — {len(own_images)}, сторонние — {len(images)}",
                "Dockerfile, `deploy/`, `.github/workflows/`",
            ),
            ("Действия GitHub Actions", str(len(actions)), "сборка и проверки в CI", "CI"),
            ("Шрифты", str(len(fonts)), "PDF отчётов", "`backend/app/modules`"),
        ],
    )
    out += [
        "",
        "Python-пакеты считаются парами «пакет — версия»: если проекты закрепили разные "
        "версии одного пакета, в перечне две строки. Интерфейс собирается в статические "
        "файлы, поэтому npm-пакеты нужны только при сборке; в браузер попадает код пакетов "
        "без признака `dev`, которые импортирует приложение. Признак `dev` ставит npm в "
        "`package-lock.json`: пакет из `devDependencies` может его не иметь, если он нужен "
        "другому пакету без этого признака (так у `vite` — peer-зависимости "
        "`@tailwindcss/vite`).",
        "",
    ]

    # распределение лицензий
    counts: dict[str, list[int]] = {}

    def count(license_: str, column: int) -> None:
        counts.setdefault(license_.rstrip(KNOWN_MARK) or "не указана", [0, 0, 0])[column] += 1

    for e in shipped_py + dev_py:
        count(py_license(e.name, py_licenses), 0)
    for e in npm:
        count(e.license, 1)
    for ref in images:
        count(IMAGE_INFO.get(split_image(ref)[0], ("",))[0], 2)
    out += ["### Лицензии", ""]
    out += _table(
        ("Лицензия", "Python", "npm", "Образы"),
        [
            (name, *(str(n) if n else "" for n in row))
            for name, row in sorted(counts.items(), key=lambda kv: (-sum(kv[1]), kv[0]))
        ],
    )
    notable = _notable(shipped_py + dev_py, py_licenses, npm, images)
    if notable:
        out += [
            "",
            "Пакеты и образы с copyleft-лицензиями, лицензиями шрифтов и без указанной "
            "лицензии. Все используются без изменений: библиотеки подключаются из "
            "реестров пакетов, образы запускаются как отдельные сервисы.",
            "",
        ]
        out += _table(("Компонент", "Версия", "Лицензия", "Где"), notable)
    out += [
        "",
        f"Лицензии с пометкой {KNOWN_MARK} — справочные: пакета нет в окружении генератора "
        "(для образов и действий CI — лицензия основного ПО). Остальные лицензии Python "
        "взяты из метаданных установленных пакетов (`License-Expression`, `License`, "
        "классификаторы), npm — из `package-lock.json`.",
        "",
    ]

    # Python
    out += [
        "## Python",
        "",
        "Проекты: "
        + ", ".join(f"{p} (`{roots[p]}`, `{d}`)" for p, d in PY_PROJECTS)
        + f". Маркеры зависимостей вычислены для платформы образов: {TARGET_LABEL}.",
        "",
    ]

    def py_rows(entries: list[PyEntry], purpose: bool) -> list[tuple[str, ...]]:
        rows = []
        for e in entries:
            row = (e.name, e.version, py_license(e.name, py_licenses), _where(e))
            rows.append((*row, PY_PURPOSES.get(canonical(e.name), "")) if purpose else row)
        return rows

    direct = [e for e in shipped_py if e.kind == KIND_DIRECT]
    transitive = [e for e in shipped_py if e.kind == KIND_TRANSITIVE]
    out += [f"### Прямые зависимости — {len(direct)}", ""]
    out += _table(("Пакет", "Версия", "Лицензия", "Где", "Назначение"), py_rows(direct, True))
    out += ["", f"### Транзитивные зависимости — {len(transitive)}", ""]
    out += _table(("Пакет", "Версия", "Лицензия", "Где", "Назначение"), py_rows(transitive, True))
    out += ["", f"### Для разработки и тестов — {len(dev_py)}", ""]
    out += _table(("Пакет", "Версия", "Лицензия", "Где", "Назначение"), py_rows(dev_py, True))
    if excluded_py:
        out += [
            "",
            f"### Не ставятся на {TARGET_LABEL} — {len(excluded_py)}",
            "",
            "Есть в lock-файлах для других версий Python и ОС; в образы не попадают.",
            "",
        ]
        out += _table(
            ("Пакет", "Версия", "Лицензия", "Условие установки", "Где"),
            [
                (
                    e.name,
                    e.version,
                    py_license(e.name, py_licenses),
                    "; ".join(sorted(set(e.excluded.values()))),
                    ", ".join(p for p, _ in PY_PROJECTS if p in e.excluded),
                )
                for e in excluded_py
            ],
        )

    # npm
    out += [
        "",
        "## npm (frontend)",
        "",
        "Прямые зависимости — из `frontend/package.json`; транзитивные «сборки» — пакеты с "
        "признаком `dev` в `package-lock.json`, нужные только для сборки и проверки типов.",
        "",
    ]
    groups = (
        (
            "Прямые зависимости (`dependencies`)",
            [e for e in npm_main if e.direct == "dependencies"],
        ),
        (
            "Прямые зависимости сборки (`devDependencies`)",
            [e for e in npm_main if e.direct == "devDependencies"],
        ),
        ("Транзитивные зависимости", [e for e in npm_runtime if not e.direct]),
        ("Транзитивные зависимости сборки", [e for e in npm_dev if not e.direct]),
    )
    for title, entries in groups:
        out += [f"### {title} — {len(entries)}", ""]
        out += _table(
            ("Пакет", "Версия", "Лицензия", "Назначение"),
            [(e.name, e.version, e.license, npm_purpose(e.name)) for e in entries],
        )
        out.append("")
    out += [
        f"### Платформенные бинарные сборки — {len(npm_platform)}",
        "",
        "Необязательные пакеты с бинарниками инструментов сборки (esbuild, Rollup, "
        "Tailwind, Lightning CSS): npm ставит только сборку под текущую ОС и процессор.",
        "",
    ]
    out += _table(
        ("Пакет", "Версия", "Лицензия", "ОС / процессор"),
        [(e.name, e.version, e.license, e.platform) for e in npm_platform],
    )

    # образы
    out += ["", "## Образы контейнеров", "", "### Собственные образы", ""]
    out += _table(
        ("Образ", "Dockerfile", "Базовые образы"),
        [
            (name, f"{_dockerfile_of(name)}", ", ".join(bases))
            for name, bases in sorted(own_images.items())
        ],
    )
    out += ["", "### Сторонние образы", ""]
    rows = []
    for ref in sorted(images):
        repo, tag = split_image(ref)
        license_, purpose = IMAGE_INFO.get(repo, ("", ""))
        use = images[ref]
        if tag.startswith("sha256:"):
            tag = f"{tag[:19]}…" + (f" ({use.version_note})" if use.version_note else "")
        rows.append(
            (
                repo,
                tag,
                license_ + KNOWN_MARK if license_ else "",
                purpose,
                ", ".join(f"`{s}`" for s in sorted(use.sources)),
            )
        )
    out += _table(("Образ", "Тег", "Лицензия", "Назначение", "Где используется"), rows)

    out += ["", "## Шрифты", ""]
    out += _table(("Файлы", "Версия", "Лицензия", "Назначение"), fonts)
    out += [
        "",
        "Шрифт Inter интерфейса — npm-пакет `@fontsource-variable/inter` в таблице npm.",
        "",
        "## CI: действия GitHub Actions",
        "",
    ]
    out += _table(
        ("Действие", "Версия", "Лицензия", "Назначение", "Где"),
        [
            (
                ref.split("@")[0],
                ref.split("@")[1],
                ACTION_INFO.get(ref.split("@")[0], ("", ""))[0] + KNOWN_MARK,
                ACTION_INFO.get(ref.split("@")[0], ("", ""))[1],
                ", ".join(f"`{s}`" for s in sorted(sources)),
            )
            for ref, sources in sorted(actions.items())
        ],
    )

    out += ["", "## Сверка `backend/requirements.txt` с `uv.lock`", ""]
    if drift:
        out += [
            "Образ backend собирается по `uv.lock`. Файлы `requirements*.txt` — "
            "вспомогательные пины для установки через pip. Расхождения с `uv.lock` "
            f"({TARGET_LABEL}):",
            "",
        ]
        out += _table(("Файл", "Пакет", "Пин в файле", "Версия в uv.lock"), drift)
    else:
        out.append("Пины `backend/requirements*.txt` совпадают с `uv.lock`.")
    out.append("")
    return "\n".join(out)


def _dockerfile_of(image: str) -> str:
    name = image.removeprefix(_OWN_IMAGE_PREFIX)
    for candidate in (
        REPO_ROOT / name / "Dockerfile",
        REPO_ROOT / "services" / name / "Dockerfile",
    ):
        if candidate.exists():
            return f"`{_rel(candidate)}`"
    return ""


def _notable(
    python: list[PyEntry],
    py_licenses: dict[str, str],
    npm: list[NpmEntry],
    images: dict[str, ImageUse],
) -> list[tuple[str, str, str, str]]:
    rows = []
    for e in python:
        license_ = py_license(e.name, py_licenses)
        if NOTABLE_LICENSE_RE.search(license_.rstrip(KNOWN_MARK)):
            rows.append((e.name, e.version, license_ or "не указана", f"Python: {_where(e)}"))
    families: dict[tuple[str, str, str], int] = {}
    for e in npm:
        if not NOTABLE_LICENSE_RE.search(e.license):
            continue
        if e.platform:
            family = _PLATFORM_SUFFIX_RE.sub("", e.name)
            key = (family, e.version, e.license or "не указана")
            families[key] = families.get(key, 0) + 1
            continue
        rows.append((e.name, e.version, e.license or "не указана", "npm (frontend)"))
    for (family, version, license_), n in sorted(families.items()):
        rows.append((f"{family}-*", version, license_, f"npm: платформенные сборки ({n})"))
    for ref in sorted(images):
        repo, tag = split_image(ref)
        license_ = IMAGE_INFO.get(repo, ("",))[0]
        if NOTABLE_LICENSE_RE.search(license_):
            rows.append((repo, tag, (license_ or "не указана") + KNOWN_MARK, "образ"))
    return rows


def build(extra_site_packages: Iterable[Path] = ()) -> str:
    python, roots = collect_python()
    images, own_images = collect_images()
    return render(
        python,
        roots,
        installed_licenses(extra_site_packages),
        collect_npm(),
        images,
        own_images,
        collect_actions(),
        collect_fonts(),
        requirements_drift(python),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Перечень библиотек → libraries.md")
    parser.add_argument("--check", action="store_true", help="только проверить актуальность")
    parser.add_argument(
        "--site-packages",
        action="append",
        type=Path,
        default=[],
        help="дополнительный каталог site-packages для лицензий (можно несколько)",
    )
    parser.add_argument("--output", type=Path, default=OUTPUT, help="куда писать перечень")
    args = parser.parse_args(argv)
    content = build(args.site_packages)
    current = args.output.read_text(encoding="utf-8") if args.output.exists() else None
    if args.check:
        if current != content:
            sys.stderr.write(f"{args.output} устарел — запустите: {COMMAND}\n")
            return 1
        sys.stdout.write(f"{args.output}: актуален\n")
        return 0
    if current != content:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
    sys.stdout.write(f"{args.output}: {'обновлён' if current != content else 'без изменений'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
