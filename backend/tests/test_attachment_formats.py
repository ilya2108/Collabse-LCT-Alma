"""Белый список вложений заявки (файлы в статусах): 10 форматов по расширению и сигнатуре.

Чистые проверки без БД и MinIO: `check_attachment_format` и схемы тел перехода и передачи
портфеля. Сквозные сценарии (загрузка, переход с файлом, таймлайн) —
tests/db/test_request_files_portfolio_db.py.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.core.errors import ApiError
from app.modules.files.service import (
    ATTACHMENT_FORMATS,
    attachment_extension,
    check_attachment_format,
)
from app.modules.requests.schemas import (
    MAX_TRANSITION_FILES,
    PortfolioTransferBody,
    TransitionBody,
)
from app.modules.workflow.schemas import MAX_STAGE_STUCK_DAYS, StatusPatch, StatusSpec

ZIP = b"PK\x03\x04" + b"\x00" * 26
OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 24

VALID = {
    "скан.png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 8,
    "фото.jpg": b"\xff\xd8\xff\xe0" + b"\x00" * 8,
    "фото.jpeg": b"\xff\xd8\xff\xe1" + b"\x00" * 8,
    "договор.pdf": b"%PDF-1.7\n%...",
    "материалы.zip": ZIP,
    "выгрузка.gz": b"\x1f\x8b\x08\x00" + b"\x00" * 8,
    "выгрузка.gzip": b"\x1f\x8b\x08\x00" + b"\x00" * 8,
    "архив.tar.gz": b"\x1f\x8b\x08\x00" + b"\x00" * 8,
    "архив.tgz": b"\x1f\x8b\x08\x00" + b"\x00" * 8,
    "архив.rar": b"Rar!\x1a\x07\x01\x00" + b"\x00" * 8,
    "протокол.doc": OLE2,
    "протокол-rtf.doc": b"{\\rtf1\\ansi ...}",
    "протокол.docx": ZIP,
    "смета.xls": OLE2,
    "смета.xlsx": ZIP,
}

# форматы ТЗ (ФТ3): png, jpeg, pdf, zip, gzip, rar, doc, docx, xls, xlsx
TZ_FORMATS = {"png", "jpeg", "pdf", "zip", "gzip", "rar", "doc", "docx", "xls", "xlsx"}


def _code(exc: ApiError) -> str:
    assert exc.details, exc.message
    assert exc.message == "Недопустимый формат файла"
    return exc.details[0]["code"]


def _reason(exc: ApiError) -> str:
    assert exc.details
    return exc.details[0]["message"]


def test_whitelist_covers_tz_formats() -> None:
    assert TZ_FORMATS <= set(ATTACHMENT_FORMATS)
    # синонимы тех же форматов, без посторонних (html, exe, csv, txt …)
    assert set(ATTACHMENT_FORMATS) - TZ_FORMATS == {"jpg", "gz", "tgz"}


@pytest.mark.parametrize(("name", "data"), sorted(VALID.items()))
def test_valid_formats_accepted(name: str, data: bytes) -> None:
    content_type = check_attachment_format(name, data)
    assert content_type == ATTACHMENT_FORMATS[attachment_extension(name)][0]


def test_extension_case_insensitive_and_type_from_server() -> None:
    assert check_attachment_format("СКАН.PDF", b"%PDF-1.4") == "application/pdf"
    # PDF с мусором до заголовка (сканеры) — допускается в первом килобайте
    assert check_attachment_format("скан.pdf", b"\x00" * 100 + b"%PDF-1.4") == "application/pdf"


@pytest.mark.parametrize(
    "name",
    ["отчёт.exe", "страница.html", "данные.csv", "заметка.txt", "скрипт.js", "без_расширения"],
)
def test_other_formats_rejected(name: str) -> None:
    with pytest.raises(ApiError) as caught:
        check_attachment_format(name, b"MZ\x90\x00 or <html>")
    assert caught.value.status_code == 400
    assert _code(caught.value) == "file_type_not_allowed"
    assert "PNG, JPEG, PDF, ZIP, GZIP, RAR, DOC, DOCX, XLS, XLSX" in _reason(caught.value)


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("договор.pdf", b"<html><script>alert(1)</script>"),
        ("скан.png", b"MZ\x90\x00\x03"),  # исполняемый файл под видом картинки
        ("смета.xlsx", OLE2),  # старый xls, переименованный в xlsx
        ("протокол.doc", ZIP),
        ("архив.rar", ZIP),
        ("фото.jpg", b"\x89PNG\r\n\x1a\n"),
        ("договор.pdf", b"\x00" * 2000 + b"%PDF-1.4"),  # заголовок дальше 1 КБ
        ("пусто.zip", b""),
    ],
)
def test_signature_mismatch_rejected(name: str, data: bytes) -> None:
    with pytest.raises(ApiError) as caught:
        check_attachment_format(name, data)
    assert caught.value.status_code == 400
    assert _code(caught.value) == "file_type_not_allowed"
    assert "не похоже" in _reason(caught.value)


def test_attachment_extension() -> None:
    assert attachment_extension("a.tar.gz") == "gz"
    assert attachment_extension("Скан.JPEG") == "jpeg"
    assert attachment_extension("README") == ""
    assert attachment_extension("") == ""


# --- тело перехода: необязательный комментарий до 4000 символов, файлы ---------------------


def test_transition_comment_optional_and_limited() -> None:
    status_id = uuid.uuid4()
    body = TransitionBody(to_status_id=status_id, version=1)
    assert body.comment is None and body.file_ids == []
    assert TransitionBody(to_status_id=status_id, version=1, comment="к" * 4000).comment
    with pytest.raises(ValidationError):
        TransitionBody(to_status_id=status_id, version=1, comment="к" * 4001)


def test_transition_files_limited() -> None:
    ids = [uuid.uuid4() for _ in range(MAX_TRANSITION_FILES)]
    assert len(TransitionBody(to_status_id=uuid.uuid4(), version=1, file_ids=ids).file_ids) == 10
    with pytest.raises(ValidationError):
        TransitionBody(to_status_id=uuid.uuid4(), version=1, file_ids=[*ids, uuid.uuid4()])


def test_portfolio_transfer_to_other_user_only() -> None:
    same = uuid.uuid4()
    with pytest.raises(ValidationError):
        PortfolioTransferBody(from_user_id=same, to_user_id=same)
    body = PortfolioTransferBody(from_user_id=uuid.uuid4(), to_user_id=uuid.uuid4())
    assert body.include_universities is True and body.dry_run is False


# --- порог зависания этапа до 365 дней (P1-6) ---------------------------------------------


def test_stage_threshold_up_to_365_days() -> None:
    assert MAX_STAGE_STUCK_DAYS == 365
    assert StatusPatch(stuck_threshold_days=365).stuck_threshold_days == 365
    spec = StatusSpec(code="teaching", name="Ведение занятий", stuck_threshold_days=120)
    assert spec.stuck_threshold_days == 120
    with pytest.raises(ValidationError):
        StatusPatch(stuck_threshold_days=366)
    with pytest.raises(ValidationError):
        StatusPatch(stuck_threshold_days=0)
