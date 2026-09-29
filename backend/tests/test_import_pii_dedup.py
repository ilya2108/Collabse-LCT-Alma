"""ПДн в импорте: маскирование ошибок/отчётов и дедупликация по HMAC blind index."""

from __future__ import annotations

import io

from cryptography.fernet import Fernet

from app.core.pii import PiiCrypto, normalize_email
from app.modules.import_export.importers import (
    PreparedRow,
    RowIssue,
    find_file_duplicates,
    resolve_funnel_status,
)
from app.modules.import_export.mapping import STUDENT_FIELDS
from app.modules.import_export.service import _build_error_report_xlsx, mask_pii_values

HMAC_KEY = "unit-test-hmac-key-0123456789abcdef-0123456789abcdef"


def _crypto() -> PiiCrypto:
    return PiiCrypto([Fernet.generate_key().decode()], HMAC_KEY)


class TestMaskPiiValues:
    def test_masks_only_pii_fields(self) -> None:
        values = {
            "full_name": "Иванов Иван Иванович",
            "email": "ivanov@example.com",
            "phone": "+79160001122",
            "university": "МФТИ",
        }
        masked = mask_pii_values(values, STUDENT_FIELDS.pii_fields())
        assert masked["full_name"] == "И***в И.И."
        assert masked["email"] == "i***v@example.com"
        assert "***" in masked["phone"]
        assert masked["university"] == "МФТИ"  # не-ПДн остаётся как есть

    def test_none_values_stay_none(self) -> None:
        masked = mask_pii_values({"email": None}, STUDENT_FIELDS.pii_fields())
        assert masked["email"] is None


class TestErrorReportMasking:
    def test_xlsx_report_has_no_open_pii(self) -> None:
        import openpyxl

        row = PreparedRow(
            row_number=17,
            values={"full_name": "Иванов Иван Иванович", "email": "ivanov@example.com"},
            issues=[
                RowIssue(
                    row=17,
                    column="email",
                    code="invalid_format",
                    message="Некорректный email",
                )
            ],
        )
        data = _build_error_report_xlsx([row], STUDENT_FIELDS.pii_fields())
        sheet = openpyxl.load_workbook(io.BytesIO(data)).active
        cells = [str(c.value) for r in sheet.iter_rows() for c in r if c.value]
        joined = " ".join(cells)
        assert "Иванов Иван Иванович" not in joined
        assert "ivanov@example.com" not in joined
        assert "И***в И.И." in joined


class TestHmacDedup:
    """Дедупликация студентов — upsert по email_hmac (data-model.md §7.1)."""

    def test_hmac_ignores_case_and_spaces(self) -> None:
        crypto = _crypto()
        assert crypto.email_hmac("  Ivanov@Example.COM ") == crypto.email_hmac(
            "ivanov@example.com"
        )

    def test_hmac_differs_for_other_email(self) -> None:
        crypto = _crypto()
        assert crypto.email_hmac("a@example.com") != crypto.email_hmac("b@example.com")

    def test_in_file_duplicates_by_normalized_email(self) -> None:
        rows = [
            (2, normalize_email("Ivanov@Example.com")),
            (3, normalize_email("petrov@example.com")),
            (5, normalize_email("  ivanov@example.COM ")),
        ]
        issues = find_file_duplicates(rows, column="email")
        assert set(issues) == {5}
        assert issues[5].code == "duplicate_in_file"
        assert "строкой 2" in issues[5].message

    def test_empty_keys_not_duplicates(self) -> None:
        assert find_file_duplicates([(1, ""), (2, "")], column="email") == {}


class TestFunnelStatusDictionary:
    def test_russian_values(self) -> None:
        assert resolve_funnel_status("Учится") == "studying"
        assert resolve_funnel_status("выпускник") == "graduate"
        assert resolve_funnel_status("Кадровый резерв") == "talent_pool"
        assert resolve_funnel_status("candidate") == "candidate"

    def test_unknown_value(self) -> None:
        assert resolve_funnel_status("отчислен-неизвестно") is None
