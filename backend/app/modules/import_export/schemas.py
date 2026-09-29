"""Pydantic-схемы импорта/экспорта (api-contract.md §6–7, §10.1)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class MappingItem(_ApiModel):
    source_column: int = Field(ge=0)
    target_field: str = Field(min_length=1)
    transform: Literal["trim", "lowercase", "uppercase", "date_ru", "phone_normalize"] | None = (
        None
    )
    lookup: str | None = None
    create_missing: bool = False
    value_map: dict[str, str] = Field(default_factory=dict)


class MappingOptions(_ApiModel):
    sheet: int | str = 0
    header_row: int = Field(default=0, ge=0)
    encoding: str | None = None
    update_strategy: Literal["create_only", "upsert", "update_only"] = "upsert"
    match_by: list[str] = Field(default_factory=list)
    skip_empty_rows: bool = True
    default_values: dict[str, Any] = Field(default_factory=dict)


class MappingBody(_ApiModel):
    mapping: list[MappingItem] = Field(min_length=1)
    options: MappingOptions = Field(default_factory=MappingOptions)


class ApplyBody(_ApiModel):
    mode: Literal["skip_errors", "all_or_nothing"] = "skip_errors"


class ExportOptions(_ApiModel):
    encoding: Literal["utf-8", "cp1251"] = "utf-8"
    csv_delimiter: str = Field(default=";", min_length=1, max_length=1)


class ExportJobBody(_ApiModel):
    entity_type: str = Field(min_length=1)
    format: Literal["xlsx", "csv", "json"] = "xlsx"
    filters: dict[str, Any] = Field(default_factory=dict)
    columns: list[str] | None = None
    options: ExportOptions = Field(default_factory=ExportOptions)


class DictionaryImportBody(_ApiModel):
    items: list[dict[str, Any]] = Field(min_length=1)
    update_strategy: Literal["create_only", "upsert", "update_only"] = "upsert"


class DictionaryItemBody(_ApiModel):
    model_config = ConfigDict(extra="allow")

    name: str | None = None
