"""Интерактивный маппинг: автопредложение по синонимам, трансформации, валидация."""

from __future__ import annotations

import pytest

from app.core.errors import ApiError
from app.modules.import_export.mapping import (
    STUDENT_FIELDS,
    apply_transform,
    entity_fields,
    map_row,
    normalize_phone,
    parse_mapping_entries,
    suggest_mapping,
    validate_mapping,
)


class TestSuggestMapping:
    def test_russian_headers(self) -> None:
        columns = ["ФИО", "Эл. почта", "ВУЗ", "Программа", "Комментарий"]
        suggestions = {
            s["source_column"]: s for s in suggest_mapping(columns, STUDENT_FIELDS)
        }
        assert suggestions[0]["target_field"] == "full_name"
        assert suggestions[1]["target_field"] == "email"
        assert suggestions[2]["target_field"] == "university"
        assert suggestions[3]["target_field"] == "program"
        assert suggestions[4]["target_field"] == "notes"

    def test_exact_match_more_confident_than_partial(self) -> None:
        exact = suggest_mapping(["ФИО"], STUDENT_FIELDS)[0]
        partial = suggest_mapping(["ФИО студента"], STUDENT_FIELDS)[0]
        assert exact["confidence"] > partial["confidence"]

    def test_field_not_suggested_twice(self) -> None:
        suggestions = suggest_mapping(["email", "почта"], STUDENT_FIELDS)
        targets = [s["target_field"] for s in suggestions]
        assert targets.count("email") == 1

    def test_unknown_header_skipped(self) -> None:
        assert suggest_mapping(["Какая-то колонка XYZQ"], STUDENT_FIELDS) == []


class TestTransforms:
    def test_date_ru(self) -> None:
        assert apply_transform("07.02.2027", "date_ru") == "2027-02-07"
        assert apply_transform("2027-02-07", "date_ru") == "2027-02-07"
        with pytest.raises(ValueError):
            apply_transform("вчера", "date_ru")

    def test_phone_normalize(self) -> None:
        assert normalize_phone("8 (916) 000-11-22") == "+79160001122"
        assert normalize_phone("916 000 11 22") == "+79160001122"
        assert normalize_phone("+7 916 000 11 22") == "+79160001122"

    def test_case_transforms(self) -> None:
        assert apply_transform("  MiXeD  ", "lowercase") == "mixed"
        assert apply_transform("  MiXeD  ", "uppercase") == "MIXED"
        assert apply_transform("  MiXeD  ", "trim") == "MiXeD"

    def test_unknown_transform(self) -> None:
        with pytest.raises(ValueError):
            apply_transform("x", "reverse")


class TestMapRow:
    def _entries(self) -> list:
        return parse_mapping_entries(
            [
                {"source_column": 0, "target_field": "full_name"},
                {"source_column": 1, "target_field": "email", "transform": "lowercase"},
                {
                    "source_column": 2,
                    "target_field": "funnel_status",
                    "value_map": {"Учится": "studying"},
                },
            ]
        )

    def test_transform_and_value_map(self) -> None:
        values, errors = map_row(
            ["Иванов Иван", "IVANOV@Example.COM", "учится"], self._entries()
        )
        assert errors == []
        assert values["email"] == "ivanov@example.com"
        # ключи value_map сравниваются без регистра
        assert values["funnel_status"] == "studying"

    def test_default_values_fill_blanks(self) -> None:
        values, _ = map_row(
            ["Иванов Иван", "i@example.com", None],
            self._entries(),
            {"funnel_status": "candidate"},
        )
        assert values["funnel_status"] == "candidate"

    def test_transform_error_collected_per_row(self) -> None:
        entries = parse_mapping_entries(
            [{"source_column": 0, "target_field": "starts_on", "transform": "date_ru"}]
        )
        values, errors = map_row(["не дата"], entries)
        assert values["starts_on"] is None
        assert errors[0]["code"] == "transform_error"


class TestValidateMapping:
    def test_required_field_missing(self) -> None:
        entries = parse_mapping_entries([{"source_column": 0, "target_field": "email"}])
        with pytest.raises(ApiError) as exc:
            validate_mapping(entries, 3, STUDENT_FIELDS)
        codes = {d["code"] for d in exc.value.details or []}
        assert "required_field_missing" in codes  # full_name не замаплен

    def test_unknown_field_and_column_range(self) -> None:
        entries = parse_mapping_entries(
            [
                {"source_column": 0, "target_field": "full_name"},
                {"source_column": 9, "target_field": "email"},
                {"source_column": 1, "target_field": "nonexistent"},
            ]
        )
        with pytest.raises(ApiError) as exc:
            validate_mapping(entries, 2, STUDENT_FIELDS)
        codes = {d["code"] for d in exc.value.details or []}
        assert {"column_out_of_range", "unknown_target_field"} <= codes

    def test_duplicate_target(self) -> None:
        entries = parse_mapping_entries(
            [
                {"source_column": 0, "target_field": "full_name"},
                {"source_column": 1, "target_field": "full_name"},
                {"source_column": 2, "target_field": "email"},
            ]
        )
        with pytest.raises(ApiError) as exc:
            validate_mapping(entries, 3, STUDENT_FIELDS)
        codes = {d["code"] for d in exc.value.details or []}
        assert "duplicate_target_field" in codes


class TestEntityRegistry:
    def test_supported_types(self) -> None:
        assert entity_fields("students").entity_type == "students"
        assert entity_fields("dictionary:regions").default_match_by == ("name",)

    def test_unsupported_type_lists_available(self) -> None:
        with pytest.raises(ApiError) as exc:
            entity_fields("contracts")
        assert exc.value.code == "validation_error"
        assert "students" in (exc.value.details or [])[0]["message"]


class TestDictionaryProducts:
    """Справочник продуктов из файла организаторов «Вендоры.xlsx»."""

    def test_registered_in_entity_types(self) -> None:
        from app.modules.import_export.mapping import supported_entity_types

        assert "dictionary:products" in supported_entity_types()
        spec = entity_fields("dictionary:products")
        assert spec.required_fields() == ("name",)
        assert spec.default_match_by == ("name",)
        # контакты (ФИО/телефон/почта) — ПДн: целевых полей для них нет
        assert set(spec.by_name()) == {"name", "vendor"}

    def test_unsupported_type_error_lists_products(self) -> None:
        with pytest.raises(ApiError) as exc:
            entity_fields("contracts")
        assert "dictionary:products" in (exc.value.details or [])[0]["message"]

    def test_vendor_headers_suggested(self) -> None:
        spec = entity_fields("dictionary:products")
        columns = ["Компания", "Продукт", "ФИО", "Телефон", "Почта", "Способ связи"]
        suggestions = {
            s["source_column"]: s["target_field"] for s in suggest_mapping(columns, spec)
        }
        assert suggestions[0] == "vendor"
        assert suggestions[1] == "name"
        # ПДн-колонки не предлагаются вовсе
        assert set(suggestions) == {0, 1}

    def test_split_product_names_comma_and_quotes(self) -> None:
        from app.modules.import_export.importers import split_product_names

        assert split_product_names("«RT.DataLake», «RT.Warehouse»") == [
            "RT.DataLake",
            "RT.Warehouse",
        ]
        assert split_product_names("«Базис Dynamix»") == ["Базис Dynamix"]
        assert split_product_names("A, a, ,B") == ["A", "B"]
        assert split_product_names("") == []

    def test_product_code_from_name(self) -> None:
        from app.modules.import_export.importers import product_code_from_name

        assert product_code_from_name("RT.DataLake") == "rt_datalake"
        assert product_code_from_name("Аврора SDK") == "аврора_sdk"

    def test_vendors_xlsx_roundtrip(self) -> None:
        """XLSX как у организаторов: несколько продуктов через запятую в одной ячейке."""
        import io

        import openpyxl

        from app.modules.import_export.importers import split_product_names
        from app.modules.import_export.parsers import parse_table

        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.append(["Компания", "Продукт", "ФИО", "Телефон", "Почта", "Способ связи"])
        sheet.append(
            [
                "ООО «ТДата»",
                "«RT.DataLake», «RT.Warehouse»",
                "Смирнова Анна Петровна",
                "+7 (911) 222-33-44",
                "smirnova.ap@example.ru",
                "Чат в ТГ",
            ]
        )
        buffer = io.BytesIO()
        workbook.save(buffer)

        spec = entity_fields("dictionary:products")
        table = parse_table(buffer.getvalue())
        suggested = suggest_mapping(table.columns, spec)
        entries = parse_mapping_entries(
            [
                {"source_column": s["source_column"], "target_field": s["target_field"]}
                for s in suggested
            ]
        )
        validate_mapping(entries, len(table.columns), spec)
        values, errors = map_row(table.rows[0], entries)
        assert errors == []
        assert values["vendor"] == "ООО «ТДата»"
        assert split_product_names(values["name"] or "") == ["RT.DataLake", "RT.Warehouse"]
