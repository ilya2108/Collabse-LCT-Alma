"""Воронка студента (чистые правила §8.2) и писатели экспорта с кодировками (§7.1)."""

from __future__ import annotations

import io

import orjson

from app.modules.import_export.export import render_export
from app.modules.talent_pool.service import validate_funnel_transition

COLUMNS = [("name", "Название"), ("city", "Город")]
ROWS = [{"name": "МФТИ", "city": "Долгопрудный"}, {"name": "МГУ", "city": "Москва"}]


class TestFunnelTransitions:
    """candidate → studying → graduate → talent_pool; назад — с комментарием."""

    def test_forward_neighbor_ok(self) -> None:
        assert validate_funnel_transition("candidate", "studying", None) is None
        assert validate_funnel_transition("graduate", "talent_pool", None) is None

    def test_forward_jump_rejected(self) -> None:
        assert validate_funnel_transition("candidate", "graduate", None) == "forward_jump"
        assert (
            validate_funnel_transition("candidate", "talent_pool", "хоть с комментарием")
            == "forward_jump"
        )

    def test_backward_requires_comment(self) -> None:
        assert (
            validate_funnel_transition("studying", "candidate", None) == "comment_required"
        )
        assert validate_funnel_transition("studying", "candidate", "  ") == "comment_required"
        assert validate_funnel_transition("studying", "candidate", "отчислен") is None
        # назад можно на любой статус, не только соседний
        assert validate_funnel_transition("talent_pool", "candidate", "ошибка ввода") is None

    def test_same_and_unknown(self) -> None:
        assert validate_funnel_transition("studying", "studying", None) == "same_status"
        assert validate_funnel_transition("studying", "expelled", None) == "unknown_status"


class TestExportWriters:
    def test_csv_utf8_has_bom_and_semicolon(self) -> None:
        data = render_export(COLUMNS, ROWS, export_format="csv")
        assert data.startswith(b"\xef\xbb\xbf")  # BOM — Excel читает кириллицу
        text = data.decode("utf-8-sig")
        assert text.splitlines()[0] == "Название;Город"
        assert "МФТИ;Долгопрудный" in text

    def test_csv_cp1251(self) -> None:
        data = render_export(
            COLUMNS, ROWS, export_format="csv", encoding="cp1251", delimiter=";"
        )
        text = data.decode("cp1251")
        assert "МГУ;Москва" in text

    def test_csv_custom_delimiter(self) -> None:
        data = render_export(COLUMNS, ROWS, export_format="csv", delimiter=",")
        assert "МФТИ,Долгопрудный" in data.decode("utf-8-sig")

    def test_xlsx_roundtrip(self) -> None:
        import openpyxl

        data = render_export(COLUMNS, ROWS, export_format="xlsx")
        sheet = openpyxl.load_workbook(io.BytesIO(data)).active
        assert [c.value for c in sheet[1]] == ["Название", "Город"]
        assert sheet["A2"].value == "МФТИ"

    def test_json_structure(self) -> None:
        payload = orjson.loads(render_export(COLUMNS, ROWS, export_format="json"))
        assert payload["columns"][0] == {"key": "name", "label": "Название"}
        assert payload["items"][1]["city"] == "Москва"
