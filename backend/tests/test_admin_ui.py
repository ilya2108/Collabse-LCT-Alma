"""Админка и UI-инфраструктура: фичефлаги, системные настройки, пресеты."""

from __future__ import annotations

import pytest

from app.core.errors import ApiError
from app.modules.admin.service import (
    KNOWN_FLAGS,
    merge_flags,
    validate_system_settings,
)
from app.modules.ui.presets import (
    add_preset,
    filter_presets,
    patch_preset,
    remove_preset,
)


class TestFeatureFlags:
    def test_defaults_all_off(self) -> None:
        flags = merge_flags(None)
        assert flags == {name: spec["default"] for name, spec in KNOWN_FLAGS.items()}
        assert flags["report_pdf"] is False  # Р-13: PDF вне MVP
        assert flags["llm_features"] is False  # N6: LLM в закрытом контуре выключен

    def test_stored_override_and_unknown_dropped(self) -> None:
        flags = merge_flags({"auto_ranking": True, "time_travel": True})
        assert flags["auto_ranking"] is True
        assert "time_travel" not in flags


class TestSystemSettingsValidation:
    def test_valid_full_document(self) -> None:
        result = validate_system_settings(
            {
                "stuck_threshold_days": 21,
                "stuck_escalate_to_manager": False,
                "integration_mode": "stream",
                "file_delivery": "presigned",
                "version": 3,
            }
        )
        assert result == {
            "stuck_threshold_days": 21,
            "stuck_escalate_to_manager": False,
            "integration_mode": "stream",
            "file_delivery": "presigned",
        }

    @pytest.mark.parametrize("days", [0, 61, "14", None])
    def test_threshold_bounds(self, days: object) -> None:
        with pytest.raises(ApiError) as exc_info:
            validate_system_settings({"stuck_threshold_days": days})
        assert exc_info.value.code == "validation_error"

    def test_enum_fields(self) -> None:
        with pytest.raises(ApiError):
            validate_system_settings({"integration_mode": "grpc"})
        with pytest.raises(ApiError):
            validate_system_settings({"file_delivery": "ftp"})


class TestPresets:
    def test_add_and_default_uniqueness_per_screen(self) -> None:
        items: list[dict] = []
        first = add_preset(
            items,
            {"screen": "registry.universities", "name": "Мои вузы", "is_default": True},
        )
        second = add_preset(
            items,
            {"screen": "registry.universities", "name": "ЦФО", "is_default": True},
        )
        other_screen = add_preset(
            items, {"screen": "board.b2b", "name": "Доска", "is_default": True}
        )
        by_id = {item["id"]: item for item in items}
        assert by_id[first["id"]]["is_default"] is False  # сброшен вторым дефолтом
        assert by_id[second["id"]]["is_default"] is True
        assert by_id[other_screen["id"]]["is_default"] is True  # другой экран не тронут

    def test_patch_and_filter(self) -> None:
        items: list[dict] = []
        preset = add_preset(
            items, {"screen": "registry.universities", "name": "Мои вузы"}
        )
        patched = patch_preset(
            items, preset["id"], {"name": "Вузы ЦФО", "state": {"page_size": 50}}
        )
        assert patched["name"] == "Вузы ЦФО"
        assert patched["state"] == {"page_size": 50}
        assert filter_presets(items, "registry.universities") == items
        assert filter_presets(items, "board.b2b") == []

    def test_remove_missing_raises_not_found(self) -> None:
        items: list[dict] = []
        add_preset(items, {"screen": "s", "name": "n"})
        with pytest.raises(ApiError) as exc_info:
            remove_preset(items, "no-such-id")
        assert exc_info.value.status_code == 404

    def test_remove_keeps_others(self) -> None:
        items: list[dict] = []
        keep = add_preset(items, {"screen": "s", "name": "первый"})
        drop = add_preset(items, {"screen": "s", "name": "второй"})
        remaining = remove_preset(items, drop["id"])
        assert [item["id"] for item in remaining] == [keep["id"]]
