"""Серверный PDF отчётов на fpdf2 (чистый Python, без системных библиотек).

Кириллица и знаки ▲▼ ₽ — шрифты DejaVu Sans, вшитые в пакет (`fonts/`, лицензия
Bitstream Vera / DejaVu — `fonts/LICENSE_DEJAVU`): стандартные шрифты PDF кириллицу не
поддерживают. Импорт fpdf ленивый (как openpyxl в выгрузке): без библиотеки ломается
только PDF, остальной экспорт работает.

Примитивы `ReportPdf`: шапка с полосой бренда, колонтитул «стр. N/M», сетка KPI с
дельтами, маркированный список, таблица с повтором шапки на каждой странице и
горизонтальный бар с осью от нуля.
"""

from __future__ import annotations

import functools
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FONT_DIR = Path(__file__).resolve().parent / "fonts"
FONT_FAMILY = "DejaVu"

# фирменные цвета (docs/design/brand-rt.md, frontend/src/shared/config/tokens.ts)
BRAND = "#1F69FF"  # base-info, синий РТК (= primary интерфейса)
TEXT = "#101828"
MUTED = "#585D69"
BORDER = "#E8E8EE"
TINT = "#F4F8FF"
TINT_2 = "#E9F0FF"
SUCCESS = "#067A37"
DANGER = "#B31B1B"
WARNING = "#98640A"

EMPTY = "—"


def rgb(color: str | None, default: str = BRAND) -> tuple[int, int, int]:
    value = (color or default).lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    try:
        return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except ValueError:
        return rgb(default)


def text(value: Any) -> str:
    if value is None or value == "":
        return EMPTY
    return str(value)


@dataclass(frozen=True)
class KpiItem:
    label: str
    value: str
    delta: str | None = None  # уже со знаком: «+2», «−1,5 п.п.»
    positive: bool | None = None  # рост показателя (для стрелки), None — без стрелки
    inverted: bool = False  # рост — это плохо (отказы, зависшие)
    hint: str | None = None  # мелкая подпись под значением


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    weight: float = 1.0
    align: str = "L"  # L | R | C


@functools.cache
def _report_pdf_class() -> type:
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos
    from fpdf.fonts import FontFace

    class ReportPdf(FPDF):
        """A4 с фирменной шапкой и колонтитулом «Альма · {title} · стр. N/M»."""

        def __init__(
            self, *, title: str, subtitle: str = "", generated: str = "", orientation: str = "P"
        ) -> None:
            super().__init__(orientation=orientation, unit="mm", format="A4")
            self.report_title = title
            self.report_subtitle = subtitle
            self.generated = generated
            self.add_font(FONT_FAMILY, "", str(FONT_DIR / "DejaVuSans.ttf"))
            self.add_font(FONT_FAMILY, "B", str(FONT_DIR / "DejaVuSans-Bold.ttf"))
            self.set_margins(14, 18, 14)
            self.set_auto_page_break(auto=True, margin=16)
            self.set_title(title)
            self.set_author("Альма — CRM взаимодействия с вузами")
            self.set_creator("Альма")
            self.alias_nb_pages()
            self.set_text_color(*rgb(TEXT))

        # --- колонтитулы ---------------------------------------------------------------

        def header(self) -> None:
            self.set_fill_color(*rgb(BRAND))
            self.rect(0, 0, self.w, 9, style="F")
            self.set_xy(self.l_margin, 2)
            self.set_font(FONT_FAMILY, "B", 9)
            self.set_text_color(255, 255, 255)
            self.cell(30, 5, "Альма")
            self.set_font(FONT_FAMILY, "", 8)
            self.cell(
                self.epw - 30, 5, self.report_title, align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT
            )
            self.set_text_color(*rgb(TEXT))
            self.set_y(14)

        def footer(self) -> None:
            self.set_y(-11)
            self.set_draw_color(*rgb(BORDER))
            self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
            self.set_font(FONT_FAMILY, "", 7)
            self.set_text_color(*rgb(MUTED))
            parts = ["Альма", self.report_title, f"стр. {self.page_no()}/{{nb}}"]
            if self.generated:
                parts.append(f"сформировано {self.generated}")
            self.cell(0, 6, " · ".join(parts), align="C")
            self.set_text_color(*rgb(TEXT))

        # --- текст ---------------------------------------------------------------------

        def title_block(self, title: str, lines: Sequence[str] = ()) -> None:
            self.set_font(FONT_FAMILY, "B", 16)
            self.multi_cell(0, 8, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.set_font(FONT_FAMILY, "", 9)
            self.set_text_color(*rgb(MUTED))
            for line in lines:
                if line:
                    self.multi_cell(0, 4.6, line, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.set_text_color(*rgb(TEXT))
            self.ln(3)

        def section(self, title: str, note: str | None = None, *, keep: float = 18) -> None:
            """Заголовок раздела; `keep` — сколько мм нужно под ним, чтобы не остаться
            одиноким внизу страницы (заголовок переносится вместе с содержимым)."""
            if self.will_page_break(keep):
                self.add_page()
            self.ln(2)
            self.set_font(FONT_FAMILY, "B", 11.5)
            self.multi_cell(0, 6, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            if note:
                self.set_font(FONT_FAMILY, "", 7.5)
                self.set_text_color(*rgb(MUTED))
                self.multi_cell(0, 3.8, note, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                self.set_text_color(*rgb(TEXT))
            self.ln(1.5)

        def paragraph(self, value: str, *, size: float = 8.5, color: str = MUTED) -> None:
            self.set_font(FONT_FAMILY, "", size)
            self.set_text_color(*rgb(color))
            self.multi_cell(0, size * 0.5, value, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.set_text_color(*rgb(TEXT))

        def bullets(self, lines: Sequence[str]) -> None:
            self.set_font(FONT_FAMILY, "", 9)
            for line in lines:
                if self.will_page_break(6):
                    self.add_page()
                y = self.get_y()
                self.set_text_color(*rgb(BRAND))
                self.cell(5, 5, "•")
                self.set_text_color(*rgb(TEXT))
                self.set_xy(self.l_margin + 5, y)
                self.multi_cell(self.epw - 5, 5, line, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                self.ln(0.8)
            self.ln(1)

        # --- KPI -----------------------------------------------------------------------

        def kpi_grid(self, items: Sequence[KpiItem], *, columns: int = 4) -> None:
            gap = 3.0
            width = (self.epw - gap * (columns - 1)) / columns
            height = 21.0
            for start in range(0, len(items), columns):
                if self.will_page_break(height + gap):
                    self.add_page()
                y = self.get_y()
                for index, item in enumerate(items[start : start + columns]):
                    x = self.l_margin + index * (width + gap)
                    self.set_draw_color(*rgb(BORDER))
                    self.set_fill_color(*rgb(TINT))
                    self.rect(x, y, width, height, style="DF")
                    self.set_xy(x + 2.5, y + 2)
                    self.set_font(FONT_FAMILY, "", 7.5)
                    self.set_text_color(*rgb(MUTED))
                    self.cell(width - 5, 4, item.label)
                    self.set_xy(x + 2.5, y + 6.5)
                    self.set_font(FONT_FAMILY, "B", 14)
                    self.set_text_color(*rgb(TEXT))
                    self.cell(width - 5, 7, item.value)
                    self.set_xy(x + 2.5, y + 14.5)
                    self.set_font(FONT_FAMILY, "", 7.5)
                    if item.delta is not None and item.positive is not None:
                        good = item.positive != item.inverted
                        self.set_text_color(*rgb(SUCCESS if good else DANGER))
                        arrow = "▲" if item.positive else "▼"
                        self.cell(width - 5, 4, f"{arrow} {item.delta}")
                    else:
                        self.set_text_color(*rgb(MUTED))
                        self.cell(width - 5, 4, item.hint or (item.delta or "без изменений"))
                    self.set_text_color(*rgb(TEXT))
                self.set_xy(self.l_margin, y + height + gap)
            self.ln(1)

        # --- таблицы и бары ------------------------------------------------------------

        def data_table(
            self,
            columns: Sequence[Column],
            rows: Sequence[dict[str, Any]],
            *,
            font_size: float = 7.5,
            empty_text: str = "Нет данных за период",
        ) -> None:
            if not rows:
                self.paragraph(empty_text)
                self.ln(2)
                return
            total = sum(column.weight for column in columns) or 1.0
            widths = [self.epw * column.weight / total for column in columns]
            self.set_font(FONT_FAMILY, "", font_size)
            self.set_draw_color(*rgb(BORDER))
            self.set_fill_color(255, 255, 255)
            headings = FontFace(
                family=FONT_FAMILY,
                emphasis="BOLD",
                size_pt=font_size,
                color=rgb(TEXT),
                fill_color=rgb(TINT_2),
            )
            with self.table(
                col_widths=widths,
                width=self.epw,
                headings_style=headings,
                line_height=font_size * 0.5,
                text_align=tuple(column.align for column in columns),
                repeat_headings=1,
                padding=(1.0, 1.2, 1.0, 1.2),
                cell_fill_color=rgb(TINT),
                cell_fill_mode="ROWS",
            ) as table:
                heading = table.row()
                for column in columns:
                    heading.cell(column.label)
                for item in rows:
                    row = table.row()
                    for column in columns:
                        row.cell(text(item.get(column.key)))
            self.ln(3)

        def hbar(
            self,
            label: str,
            value: float,
            max_value: float,
            color_hex: str | None,
            note: str,
            *,
            label_width: float = 48,
            bar_width: float = 52,
        ) -> None:
            """Горизонтальный бар: ось от нуля, длина пропорциональна value / max_value."""
            height = 6.0
            if self.will_page_break(height + 1):
                self.add_page()
            y = self.get_y()
            self.set_font(FONT_FAMILY, "", 8)
            self.set_xy(self.l_margin, y)
            self.cell(label_width, height, label if len(label) <= 34 else f"{label[:33]}…")
            x0 = self.l_margin + label_width
            self.set_fill_color(*rgb(TINT))
            self.rect(x0, y + 1, bar_width, height - 2, style="F")
            share = (value / max_value) if max_value > 0 else 0.0
            if share > 0:
                self.set_fill_color(*rgb(color_hex))
                self.rect(x0, y + 1, max(bar_width * min(share, 1.0), 0.6), height - 2, style="F")
            self.set_xy(x0 + bar_width + 2, y)
            self.set_font(FONT_FAMILY, "", 7.5)
            self.set_text_color(*rgb(MUTED))
            self.cell(self.epw - label_width - bar_width - 2, height, note)
            self.set_text_color(*rgb(TEXT))
            self.set_xy(self.l_margin, y + height)

        def to_bytes(self) -> bytes:
            return bytes(self.output())

    return ReportPdf


def make_report_pdf(
    *, title: str, subtitle: str = "", generated: str = "", orientation: str = "P"
) -> Any:
    """Экземпляр `ReportPdf` (класс строится лениво — fpdf импортируется при первом PDF)."""
    pdf = _report_pdf_class()(
        title=title, subtitle=subtitle, generated=generated, orientation=orientation
    )
    pdf.add_page()
    return pdf


def render_table_pdf(
    title: str,
    columns: Sequence[tuple[str, str]],
    rows: Sequence[dict[str, Any]],
    *,
    subtitle: str = "",
    generated: str = "",
) -> bytes:
    """Общий табличный PDF (выгрузка `report:<code>` дашборда): заголовок + таблица."""
    orientation = "L" if len(columns) > 6 else "P"
    pdf = make_report_pdf(
        title=title, subtitle=subtitle, generated=generated, orientation=orientation
    )
    pdf.title_block(title, [subtitle] if subtitle else [])
    pdf.data_table([Column(key, label) for key, label in columns], list(rows))
    return pdf.to_bytes()
