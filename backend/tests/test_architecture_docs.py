"""Архитектура в Archi и перечень библиотек (находка критики P1-16).

- `docs/architecture/archi/`: CSV для «Import from CSV» Archi и `alma.archimate` с двумя
  видами генерирует `build_archi.py`. Тесты проверяют, что файлы актуальны, CSV в формате
  Archi, все связи допустимы в ArchiMate, вид ссылается только на существующие элементы и
  связи, а свойства сервисов (API, экраны, каталоги, права) совпадают с кодом.
- `docs/architecture/libraries.md` генерирует `app.tools.list_libraries`. Лицензии зависят от
  окружения, поэтому вместо побайтного сравнения проверяется полнота: каждый пакет
  lock-файлов и каждый сторонний образ есть в перечне с той же версией.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import re
import sys
import xml.etree.ElementTree as ET
from email.message import Message
from pathlib import Path

import pytest

from app.core import rbac_catalog
from app.tools import list_libraries as libs

REPO_ROOT = Path(__file__).resolve().parents[2]
ARCHI_DIR = REPO_ROOT / "docs" / "architecture" / "archi"
LIBRARIES_MD = REPO_ROOT / "docs" / "architecture" / "libraries.md"
FRONTEND_ROUTER = REPO_ROOT / "frontend" / "src" / "app" / "router" / "index.tsx"

# пути API из параллельных доработок: до слияния их ещё нет в OpenAPI этой ветки
_AWAITING_MERGE: set[str] = set()

XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"


def _load_build_archi():
    spec = importlib.util.spec_from_file_location("alma_build_archi", ARCHI_DIR / "build_archi.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


archi = _load_build_archi()


@pytest.fixture(scope="module")
def generated():
    return archi.generate()


def _read_csv(name: str) -> list[list[str]]:
    with (ARCHI_DIR / name).open(encoding="utf-8", newline="") as fh:
        return list(csv.reader(fh))


# --- Archi: актуальность и формат CSV ----------------------------------------------------------


def test_archi_files_are_up_to_date(generated):
    _model, outputs = generated
    stale = [
        name
        for name, content in outputs.items()
        if (ARCHI_DIR / name).read_text(encoding="utf-8") != content
    ]
    assert not stale, f"устарели {stale}: python3 docs/architecture/archi/build_archi.py"


def test_archi_csv_headers_match_archi_importer():
    # CSVConstants Archi: заголовок сверяется по колонкам, без учёта регистра
    assert _read_csv("elements.csv")[0] == ["ID", "Type", "Name", "Documentation", "Specialization"]
    assert _read_csv("relations.csv")[0] == [
        "ID",
        "Type",
        "Name",
        "Documentation",
        "Source",
        "Target",
        "Specialization",
    ]
    assert _read_csv("properties.csv")[0] == ["ID", "Key", "Value"]
    raw = (ARCHI_DIR / "elements.csv").read_text(encoding="utf-8")
    assert raw.startswith('"ID","Type"'), "все поля в кавычках, без BOM"


def test_archi_csv_references_and_relations_are_valid():
    elements = {row[0]: row[1] for row in _read_csv("elements.csv")[1:]}
    assert elements.pop(archi.MODEL_ID) == "ArchimateModel"
    id_re = re.compile(r"^[a-zA-Z0-9._-]+$")  # CSVImporter.checkIDForInvalidCharacters
    assert all(id_re.match(el_id) for el_id in elements)
    assert set(elements.values()) <= set(archi.FOLDER_OF_TYPE)

    relations = _read_csv("relations.csv")[1:]
    ids = [row[0] for row in relations]
    assert len(ids) == len(set(ids)), "дубли id связей"
    for rel_id, rel_type, _name, _doc, source, target, _spec in relations:
        assert id_re.match(rel_id)
        assert source in elements and target in elements, rel_id
        letters = archi.ALLOWED_RELATIONS.get((elements[source], elements[target]), "")
        assert archi.RELATION_LETTERS[rel_type] in letters, (
            f"{rel_id}: {rel_type} {elements[source]} → {elements[target]} отвергнет Archi"
        )

    known = set(elements) | {archi.MODEL_ID}
    for row in _read_csv("properties.csv")[1:]:
        assert len(row) == 3 and row[0] in known, row


def test_archi_every_element_has_view_property():
    view_of = {
        row[0]: row[2] for row in _read_csv("properties.csv")[1:] if row[1] == archi.PROP_VIEW
    }
    elements = [row[0] for row in _read_csv("elements.csv")[2:]]
    views = {archi.VIEW_FUNCTIONAL, archi.VIEW_COMPONENT}
    assert all(view_of.get(el_id) in views for el_id in elements)


# --- Archi: файл .archimate ------------------------------------------------------------------


def test_archimate_views_reference_existing_elements_and_relations():
    root = ET.parse(ARCHI_DIR / "alma.archimate").getroot()
    assert root.get("version") == archi.ARCHI_MODEL_VERSION
    concepts = {
        el.get("id"): el
        for el in root.iter("element")
        if el.get(XSI_TYPE) != "archimate:ArchimateDiagramModel"
    }
    views = [
        el for el in root.iter("element") if el.get(XSI_TYPE) == "archimate:ArchimateDiagramModel"
    ]
    assert [v.get("name") for v in views] == [archi.VIEW_FUNCTIONAL, archi.VIEW_COMPONENT]

    for view in views:
        objects = {child.get("id"): child for child in view.iter("child")}
        incoming: dict[str, set[str]] = {}
        drawn = set()
        for obj in objects.values():
            if obj.get(XSI_TYPE) == "archimate:Note":
                continue
            element_id = obj.get("archimateElement")
            assert element_id in concepts, element_id
            assert element_id not in drawn, f"{element_id} дважды на виде {view.get('name')}"
            drawn.add(element_id)
            bounds = obj.find("bounds")
            assert bounds is not None and int(bounds.get("width")) > 0
            for conn in obj.findall("sourceConnection"):
                assert conn.get("source") == obj.get("id")
                rel = concepts[conn.get("archimateRelationship")]
                target = objects[conn.get("target")]
                assert rel.get("source") == element_id
                assert rel.get("target") == target.get("archimateElement")
                incoming.setdefault(conn.get("target"), set()).add(conn.get("id"))
        for obj_id, conn_ids in incoming.items():
            assert set(objects[obj_id].get("targetConnections", "").split()) == conn_ids


def test_archimate_and_csv_describe_same_model():
    root = ET.parse(ARCHI_DIR / "alma.archimate").getroot()
    xml_ids = {
        el.get("id")
        for el in root.iter("element")
        if el.get(XSI_TYPE) != "archimate:ArchimateDiagramModel"
    }
    csv_ids = {row[0] for row in _read_csv("elements.csv")[2:]}
    csv_ids |= {row[0] for row in _read_csv("relations.csv")[1:]}
    assert xml_ids == csv_ids


# --- Archi: свойства совпадают с кодом -------------------------------------------------------


def _props(model) -> dict[str, dict[str, str]]:
    return {el.id: dict(el.props) for el in model.elements.values()}


def test_archi_service_api_paths_exist_in_openapi(generated):
    from app.main import create_app

    openapi_paths = set(create_app().openapi()["paths"])
    model, _ = generated
    missing = []
    for el_id, props in _props(model).items():
        for token in filter(None, (t.strip() for t in props.get("API", "").split(";"))):
            prefix = token.rstrip("*")
            if token.endswith("*"):
                found = any(path.startswith(prefix) for path in openapi_paths)
            else:
                found = prefix in openapi_paths
            if not found:
                missing.append((el_id, token))
    unexpected = [(el_id, token) for el_id, token in missing if token not in _AWAITING_MERGE]
    assert not unexpected, f"пути API из модели нет в OpenAPI: {unexpected}"


def test_archi_service_permissions_exist_in_catalog(generated):
    model, _ = generated
    for el_id, props in _props(model).items():
        for code in filter(None, (c.strip() for c in props.get("Права", "").split(","))):
            assert code in rbac_catalog.PERMISSION_CODES, (el_id, code)


def test_archi_screens_exist_in_frontend_router(generated):
    model, _ = generated
    router = FRONTEND_ROUTER.read_text(encoding="utf-8")
    for el_id, props in _props(model).items():
        screen = props.get("Экран", "")
        if screen:
            assert f"'{screen.lstrip('/')}'" in router or f"'{screen}'" in router, (el_id, screen)


def test_archi_repository_paths_exist(generated):
    model, _ = generated
    keys = ("Каталог", "Манифест", "Манифесты", "Dockerfile", "Источник")
    for el_id, props in _props(model).items():
        for key in keys:
            if props.get(key):
                assert (REPO_ROOT / props[key]).exists(), (el_id, key, props[key])


def test_archi_role_assignments_follow_permission_catalog(generated):
    model, _ = generated
    assigned: dict[str, set[str]] = {}
    for rel in model.relations.values():
        if rel.type == "AssignmentRelationship" and rel.source.startswith("alma-role-"):
            assigned.setdefault(rel.source, set()).add(rel.target)
    functions = {f"alma-fn-{fn.key}" for fn in archi.FUNCTIONS}
    assert assigned["alma-role-admin"] == functions
    assert "alma-fn-access" not in assigned["alma-role-observer"]
    assert "alma-fn-workflow" not in assigned["alma-role-kam"]
    covered = set().union(*assigned.values())
    assert covered == functions, f"функции без роли: {functions - covered}"


def test_archi_component_view_covers_backend_modules(generated):
    model, _ = generated
    modules_dir = REPO_ROOT / "backend" / "app" / "modules"
    packages = {p.name for p in modules_dir.iterdir() if (p / "__init__.py").exists()}
    modelled = {
        Path(dict(el.props)["Каталог"]).name
        for el in model.elements.values()
        if el.id.startswith("alma-mod-")
    }
    assert packages <= modelled, f"модули без элемента в модели: {packages - modelled}"


# --- перечень библиотек ----------------------------------------------------------------------


def test_libraries_md_lists_every_locked_python_package():
    text = LIBRARIES_MD.read_text(encoding="utf-8")
    python, _roots = libs.collect_python()
    missing = [(e.name, e.version) for e in python if f"| {e.name} | {e.version} |" not in text]
    assert not missing, f"перегенерируйте libraries.md: {libs.COMMAND}; нет {missing}"


def test_libraries_md_lists_every_npm_package():
    text = LIBRARIES_MD.read_text(encoding="utf-8")
    lock = json.loads((REPO_ROOT / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
    missing = []
    for key, info in lock["packages"].items():
        if not key:
            continue
        name = info.get("name") or key.rsplit("node_modules/", 1)[-1]
        if f"| {name} | {info['version']} |" not in text:
            missing.append((name, info["version"]))
    assert not missing, f"перегенерируйте libraries.md: {libs.COMMAND}; нет {missing[:10]}"


def test_libraries_md_lists_every_third_party_image():
    text = LIBRARIES_MD.read_text(encoding="utf-8")
    images, own = libs.collect_images()
    assert {"crm/backend", "crm/frontend", "crm/notification"} <= set(own)
    for ref in images:
        repo, _tag = libs.split_image(ref)
        assert f"| {repo} |" in text, ref
    for repo in ("postgres", "quay.io/keycloak/keycloak", "quay.io/minio/minio", "eqalpha/keydb"):
        assert any(libs.split_image(ref)[0] == repo for ref in images), repo


def test_list_libraries_check_mode(tmp_path):
    out = tmp_path / "libraries.md"
    assert libs.main(["--output", str(out), "--check"]) == 1
    assert libs.main(["--output", str(out)]) == 0
    assert libs.main(["--output", str(out), "--check"]) == 0


def test_read_uv_lock_classifies_dependencies(tmp_path):
    lock = tmp_path / "uv.lock"
    lock.write_text(
        """
version = 1

[[package]]
name = "demo"
version = "1.0.0"
source = { virtual = "." }
dependencies = [
    { name = "web", extra = ["fast"] },
    { name = "oldcompat", marker = "python_full_version < '3.11'" },
]

[package.optional-dependencies]
dev = [{ name = "tester" }]

[[package]]
name = "web"
version = "2.0"
source = { registry = "https://pypi.org/simple" }
dependencies = [{ name = "core" }]

[package.optional-dependencies]
fast = [{ name = "speedup" }]

[[package]]
name = "core"
version = "3.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "speedup"
version = "0.1"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "oldcompat"
version = "0.9"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "tester"
version = "8.0"
source = { registry = "https://pypi.org/simple" }
dependencies = [{ name = "core" }]
""",
        encoding="utf-8",
    )
    entries, root = libs.read_uv_lock("demo", lock)
    assert root == "demo"
    kinds = {name: entry.uses.get("demo") for (name, _v), entry in entries.items()}
    assert kinds["web"] == libs.KIND_DIRECT
    assert kinds["core"] == libs.KIND_TRANSITIVE  # и у тестов тоже, но транзитивная важнее
    assert kinds["speedup"] == libs.KIND_TRANSITIVE  # через extra fast
    assert kinds["tester"] == libs.KIND_DEV
    assert kinds["oldcompat"] is None
    assert entries[("oldcompat", "0.9")].excluded == {"demo": "python_full_version < '3.11'"}


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"License-Expression": "Apache-2.0 OR BSD-3-Clause"}, "Apache-2.0 OR BSD-3-Clause"),
        ({"License": "MIT License"}, "MIT"),
        ({"License": "PSFL"}, "PSF-2.0"),
        (
            {
                "License": "Copyright (c) ...\nlong text",
                "Classifier": "License :: OSI Approved :: MIT License",
            },
            "MIT",
        ),
        ({"Classifier": "License :: OSI Approved :: Apache Software License"}, "Apache-2.0"),
        ({}, ""),
    ],
)
def test_license_from_metadata(headers, expected):
    meta = Message()
    for key, value in headers.items():
        meta[key] = value
    assert libs.license_from_metadata(meta) == expected


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("postgres:16-alpine", ("postgres", "16-alpine")),
        ("quay.io/keycloak/keycloak:26.0", ("quay.io/keycloak/keycloak", "26.0")),
        ("eqalpha/keydb@sha256:" + "a" * 64, ("eqalpha/keydb", "sha256:" + "a" * 64)),
        ("registry.local:5000/team/app:1.2", ("registry.local:5000/team/app", "1.2")),
        ("busybox", ("busybox", "")),
    ],
)
def test_split_image(ref, expected):
    assert libs.split_image(ref) == expected
