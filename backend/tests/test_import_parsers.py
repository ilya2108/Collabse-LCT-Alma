"""Распознавание форматов (magic bytes) и кодировок импорта на битых фикстурах (Р-18)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from app.core.errors import ApiError
from app.modules.import_export.parsers import (
    ENCODING_CHAIN,
    detect_encoding,
    detect_format,
    parse_table,
)

RU_TEXT = "ФИО;Почта;ВУЗ\nИванов Иван Иванович;ivanov@example.com;МФТИ\n"
RU_TEXT_KOI = "Фамилия;Город\nЁлкина Алёна;Тверь\n"

FIXTURES = Path(__file__).parent / "fixtures"
# настоящий BIFF: 2 строки данных, кириллица, дата (YYYY-MM-DD), целое, дробное,
# пустая ячейка; сгенерирован xlwt (см. checklist.md — «демо-шаг 4 грузит именно .xls»)
XLS_FIXTURE = (FIXTURES / "students.xls").read_bytes()


def _xlsx_bytes(rows: list[list[object]], sheet_name: str = "Лист1") -> bytes:
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = sheet_name
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


class TestDetectFormat:
    def test_xlsx_by_magic(self) -> None:
        assert detect_format(_xlsx_bytes([["a"]])) == "xlsx"

    def test_xls_by_ole_magic(self) -> None:
        data = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64
        assert detect_format(data) == "xls"

    def test_json_array(self) -> None:
        assert detect_format(b'  [{"a": 1}]') == "json"
        assert detect_format(b"\xef\xbb\xbf[{}]") == "json"

    def test_csv_fallback_for_text(self) -> None:
        assert detect_format(RU_TEXT.encode("cp1251")) == "csv"

    def test_binary_garbage_rejected(self) -> None:
        with pytest.raises(ApiError) as exc:
            detect_format(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        assert exc.value.code == "import_bad_format"

    def test_empty_rejected(self) -> None:
        with pytest.raises(ApiError):
            detect_format(b"")


class TestDetectEncoding:
    """Цепочка utf-8-sig → utf-8 → cp1251 → koi8-r → cp866 (api-contract.md §6.2)."""

    def test_chain_is_contractual(self) -> None:
        assert ENCODING_CHAIN == ("utf-8-sig", "utf-8", "cp1251", "koi8-r", "cp866")

    def test_utf8_bom(self) -> None:
        encoding, confidence = detect_encoding("﻿ФИО;Почта\n".encode())
        # BOM кодируется как utf-8-sig — префикс EF BB BF
        assert (encoding, confidence) == ("utf-8-sig", 1.0)

    def test_utf8_plain(self) -> None:
        encoding, _ = detect_encoding(RU_TEXT.encode("utf-8"))
        assert encoding == "utf-8"

    def test_cp1251(self) -> None:
        encoding, confidence = detect_encoding(RU_TEXT.encode("cp1251"))
        assert encoding == "cp1251"
        assert confidence >= 0.5

    def test_koi8r(self) -> None:
        encoding, _ = detect_encoding(RU_TEXT_KOI.encode("koi8-r"))
        assert encoding == "koi8-r"

    def test_cp866(self) -> None:
        encoding, _ = detect_encoding(RU_TEXT.encode("cp866"))
        assert encoding == "cp866"

    def test_undetectable_raises_encoding_error(self) -> None:
        # байты, невалидные ни в одной однобайтовой кодировке цепочки, — только utf-8-ломаные
        data = RU_TEXT.encode("utf-16")  # UTF-16 с NUL-байтами не входит в цепочку
        with pytest.raises(ApiError) as exc:
            detect_encoding(data)
        assert exc.value.code == "import_encoding_error"
        assert exc.value.details is not None


class TestParseCsv:
    def test_cp1251_with_semicolon(self) -> None:
        table = parse_table(RU_TEXT.encode("cp1251"))
        assert table.file_format == "csv"
        assert table.encoding == "cp1251"
        assert table.columns == ["ФИО", "Почта", "ВУЗ"]
        assert table.rows == [["Иванов Иван Иванович", "ivanov@example.com", "МФТИ"]]

    def test_comma_delimiter_sniffed(self) -> None:
        data = "name,email\nИванов,ivanov@example.com\n".encode()
        table = parse_table(data)
        assert table.columns == ["name", "email"]
        assert table.rows[0][1] == "ivanov@example.com"

    def test_forced_encoding_override(self) -> None:
        data = RU_TEXT.encode("cp866")
        table = parse_table(data, "csv", encoding="cp866")
        assert table.columns[0] == "ФИО"
        assert table.encoding == "cp866"

    def test_header_row_offset(self) -> None:
        data = "мусор\nФИО;Почта\nИванов;i@example.com\n".encode("cp1251")
        table = parse_table(data, "csv", header_row=1)
        assert table.columns == ["ФИО", "Почта"]
        assert table.rows == [["Иванов", "i@example.com"]]


class TestParseXlsx:
    def test_roundtrip(self) -> None:
        data = _xlsx_bytes(
            [["ФИО", "Email"], ["Иванов Иван", "ivanov@example.com"], [None, None]]
        )
        table = parse_table(data)
        assert table.file_format == "xlsx"
        assert table.columns == ["ФИО", "Email"]
        assert table.rows[0] == ["Иванов Иван", "ivanov@example.com"]
        assert table.sheets[0]["name"] == "Лист1"

    def test_broken_zip_is_bad_format(self) -> None:
        with pytest.raises(ApiError) as exc:
            parse_table(b"PK\x03\x04" + b"\x00" * 100)
        assert exc.value.code == "import_bad_format"

    def test_unknown_sheet(self) -> None:
        data = _xlsx_bytes([["a"], [1]])
        with pytest.raises(ApiError):
            parse_table(data, sheet="Нет такого листа")


class TestParseXls:
    """Настоящий BIFF через xlrd — путь демо-шага 4 (checklist.md)."""

    def test_biff_magic_detected(self) -> None:
        assert detect_format(XLS_FIXTURE) == "xls"

    def test_biff_roundtrip(self) -> None:
        table = parse_table(XLS_FIXTURE)
        assert table.file_format == "xls"
        assert table.columns == ["ФИО", "Email", "Дата рождения", "Баллы"]
        assert table.sheets == [{"index": 0, "name": "Студенты", "rows": 3}]
        # дата → ISO, целое без «.0», дробное как есть, пустая ячейка → None
        assert table.rows[0] == [
            "Иванов Иван Иванович", "ivanov@example.com", "2000-05-01", "42",
        ]
        assert table.rows[1] == ["Петрова Анна", None, "1999-12-31", "3.5"]

    def test_biff_header_row_offset(self) -> None:
        table = parse_table(XLS_FIXTURE, "xls", header_row=1)
        assert table.columns[0] == "Иванов Иван Иванович"
        assert len(table.rows) == 1

    def test_biff_unknown_sheet(self) -> None:
        with pytest.raises(ApiError) as exc:
            parse_table(XLS_FIXTURE, sheet="Нет такого листа")
        assert exc.value.code == "import_bad_format"

    def test_broken_biff_is_bad_format(self) -> None:
        with pytest.raises(ApiError) as exc:
            parse_table(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
        assert exc.value.code == "import_bad_format"


class TestImportSizeLimits:
    """Гейт 20 МБ проверяет сжатый размер; лимиты строк/ячеек ловят zip-бомбы."""

    def test_xlsx_row_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.modules.import_export import parsers

        monkeypatch.setattr(parsers, "MAX_IMPORT_ROWS", 2)
        data = _xlsx_bytes([["a"], [1], [2], [3]])
        with pytest.raises(ApiError) as exc:
            parse_table(data)
        assert exc.value.code == "import_bad_format"
        assert "слишком велик" in exc.value.message

    def test_xlsx_cell_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.modules.import_export import parsers

        monkeypatch.setattr(parsers, "MAX_IMPORT_CELLS", 3)
        data = _xlsx_bytes([["a", "b"], [1, 2], [3, 4]])
        with pytest.raises(ApiError):
            parse_table(data)

    def test_xlsx_uncompressed_size_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.modules.import_export import parsers

        monkeypatch.setattr(parsers, "MAX_XLSX_UNCOMPRESSED_BYTES", 16)
        with pytest.raises(ApiError) as exc:
            parse_table(_xlsx_bytes([["a"], [1]]))
        assert "после распаковки" in exc.value.message

    def test_xls_row_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.modules.import_export import parsers

        monkeypatch.setattr(parsers, "MAX_IMPORT_ROWS", 2)
        with pytest.raises(ApiError) as exc:
            parse_table(XLS_FIXTURE)  # в фикстуре 3 строки
        assert "слишком велик" in exc.value.message

    def test_legit_files_pass_default_limits(self) -> None:
        assert parse_table(_xlsx_bytes([["a"], [1]])).rows == [["1"]]
        assert parse_table(XLS_FIXTURE).file_format == "xls"


class TestParseJson:
    def test_array_of_objects(self) -> None:
        payload = [
            {"ФИО": "Иванов Иван", "email": "i@example.com"},
            {"ФИО": "Петров Пётр", "email": "p@example.com", "extra": 7},
        ]
        table = parse_table(json.dumps(payload, ensure_ascii=False).encode())
        assert table.file_format == "json"
        assert table.columns == ["ФИО", "email", "extra"]
        assert table.rows[0] == ["Иванов Иван", "i@example.com", None]
        assert table.rows[1][2] == "7"

    def test_items_wrapper(self) -> None:
        table = parse_table(b'{"items": [{"name": "X"}]}')
        assert table.columns == ["name"]

    def test_scalar_json_rejected(self) -> None:
        with pytest.raises(ApiError) as exc:
            parse_table(b'{"a": 1}')
        assert exc.value.code == "import_bad_format"

    def test_null_and_scalar_elements_become_row_issues(self) -> None:
        """Реальный файл организаторов «Данные оплат.json» начинается с null:
        такие элементы пропускаются построчно, а не роняют файл целиком."""
        payload = [
            None,
            {"ФИО": "Иванов Иван", "email": "i@example.com"},
            42,
            {"ФИО": "Петров Пётр"},
        ]
        table = parse_table(json.dumps(payload, ensure_ascii=False).encode())
        assert table.columns == ["ФИО", "email"]
        assert table.rows == [["Иванов Иван", "i@example.com"], ["Петров Пётр", None]]
        # номера строк — по исходному массиву (1-based + псевдозаголовок)
        assert table.row_numbers == [3, 5]
        assert [issue["code"] for issue in table.row_issues] == [
            "row_not_object",
            "row_not_object",
        ]
        assert [issue["row"] for issue in table.row_issues] == [2, 4]
        assert "null" in table.row_issues[0]["message"]

    def test_array_of_only_invalid_elements_rejected(self) -> None:
        # сообщение о формате остаётся для случая «объектов нет вообще»
        with pytest.raises(ApiError) as exc:
            parse_table(b"[null, 1, \"x\"]")
        assert exc.value.code == "import_bad_format"


class TestEncodingTiebreak:
    """koi8-r против cp1251: можибака — «чистая» кириллица с перевёрнутым регистром.

    Оба кандидата получают _non_ascii_score = 1.0, и раньше max() при равенстве
    брал первый в цепочке (cp1251) с уверенностью 1.0 — мусор проходил импорт
    молча. Теперь ничью решает доля строчных букв, а уверенность занижается,
    чтобы UI предложил проверить сэмплы (options.encoding — контрактный обход).
    """

    def test_koi8r_without_distinctive_chars_detected(self) -> None:
        # короткий файл без «ё» и типографики — раньше детектился как cp1251
        text = "Значение\nприоритетный вуз\nцелевой набор\n"
        encoding, confidence = detect_encoding(text.encode("koi8-r"))
        assert encoding == "koi8-r"
        assert confidence < 1.0  # неоднозначность не маскируется уверенностью 1.0

    def test_koi8r_students_csv_detected(self) -> None:
        # фикстура дефекта: заголовок «ФИО» становился «жйп», ФИО — мусором
        text = "ФИО;Email\nТестов1 Кодировка Юрьевич;test1@example.com\n"
        encoding, confidence = detect_encoding(text.encode("koi8-r"))
        assert encoding == "koi8-r"
        assert confidence < 1.0

    def test_cp1251_still_wins_its_own_bytes(self) -> None:
        text = "Значение\nприоритетный вуз\nцелевой набор\n"
        encoding, _confidence = detect_encoding(text.encode("cp1251"))
        assert encoding == "cp1251"

    def test_ambiguity_lowers_confidence_but_not_below_gate(self) -> None:
        encoding, confidence = detect_encoding("привет мир\n".encode("koi8-r"))
        assert encoding == "koi8-r"
        assert 0.5 <= confidence < 1.0


class TestCsvJsonLimits:
    """MAX_IMPORT_ROWS/MAX_IMPORT_CELLS действуют и для CSV/JSON (не только XLSX/XLS)."""

    def test_csv_row_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.modules.import_export import parsers

        monkeypatch.setattr(parsers, "MAX_IMPORT_ROWS", 3)
        data = ("col\n" + "\n".join(f"v{i}" for i in range(10))).encode()
        with pytest.raises(ApiError) as exc:
            parse_table(data)
        assert exc.value.code == "import_bad_format"
        assert "слишком велик" in exc.value.message

    def test_csv_cell_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.modules.import_export import parsers

        monkeypatch.setattr(parsers, "MAX_IMPORT_CELLS", 4)
        data = b"a;b;c\n1;2;3\n4;5;6\n"
        with pytest.raises(ApiError):
            parse_table(data)

    def test_csv_under_limit_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.modules.import_export import parsers

        monkeypatch.setattr(parsers, "MAX_IMPORT_ROWS", 3)
        assert parse_table(b"col\n1\n2\n").rows == [["1"], ["2"]]

    def test_json_row_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.modules.import_export import parsers

        monkeypatch.setattr(parsers, "MAX_IMPORT_ROWS", 2)
        payload = [{"a": i} for i in range(5)]
        with pytest.raises(ApiError) as exc:
            parse_table(json.dumps(payload).encode())
        assert "слишком велик" in exc.value.message

    def test_json_cell_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.modules.import_export import parsers

        monkeypatch.setattr(parsers, "MAX_IMPORT_CELLS", 3)
        payload = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
        with pytest.raises(ApiError):
            parse_table(json.dumps(payload).encode())
