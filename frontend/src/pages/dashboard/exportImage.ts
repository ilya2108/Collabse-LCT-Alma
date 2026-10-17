import { SURFACE } from '@/shared/config/tokens';

/**
 * Экспорт графиков аналитики на клиенте (ФТ1: выгрузка диаграмм в PNG и PDF):
 * — PNG одного графика с шапкой: название, период, фильтры, дата формирования —
 *   картинку можно вставить в отчёт без пояснений;
 * — PDF всей панели: титульная страница (фильтры и KPI), дальше по графику на страницу
 *   A4 альбомной ориентации.
 *
 * Без внешних библиотек: каждая страница рисуется на canvas (кириллица — шрифтом
 * браузера) и кладётся в PDF как JPEG (/DCTDecode) — минимальная структура PDF 1.4
 * собирается вручную. Ничего не уходит на сервер.
 */

const FONT = "'Rostelecom Basis', 'Inter Variable', 'Segoe UI', system-ui, sans-serif";
const BRAND = 'Альма — аналитика';

/** A4 альбомная в пунктах PDF и масштаб растра страницы (2 px на пункт). */
const PAGE_W_PT = 842;
const PAGE_H_PT = 595;
const PAGE_SCALE = 2;

export interface ChartExport {
  title: string;
  /** Подзаголовок карточки: что и за какой период показано. */
  subtitle?: string;
  /** Строки контекста: процесс, фильтры. */
  caption?: string[];
  dataUrl: string;
}

export interface CoverPage {
  title: string;
  lines: string[];
  /** Пары «показатель — значение» (KPI). */
  facts: { label: string; value: string }[];
}

export function generatedStamp(date = new Date()): string {
  return `Сформировано: ${date.toLocaleString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })}`;
}

function loadImage(dataUrl: string, title: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`Не удалось подготовить график «${title}»`));
    img.src = dataUrl;
  });
}

/** Перенос строки по словам под ширину (canvas сам не переносит). */
function wrapText(ctx: CanvasRenderingContext2D, text: string, maxWidth: number): string[] {
  const words = text.split(/\s+/).filter(Boolean);
  const lines: string[] = [];
  let line = '';
  for (const word of words) {
    const candidate = line ? `${line} ${word}` : word;
    if (ctx.measureText(candidate).width <= maxWidth || !line) {
      line = candidate;
    } else {
      lines.push(line);
      line = word;
    }
  }
  if (line) lines.push(line);
  return lines;
}

interface TextBlock {
  text: string;
  font: string;
  color: string;
  lineHeight: number;
}

/** Высота и отрисовка блока строк с переносом; `draw=false` — только измерить. */
function drawBlocks(
  ctx: CanvasRenderingContext2D,
  blocks: TextBlock[],
  x: number,
  y: number,
  maxWidth: number,
  draw: boolean,
): number {
  let cursor = y;
  for (const block of blocks) {
    ctx.font = block.font;
    ctx.fillStyle = block.color;
    for (const line of wrapText(ctx, block.text, maxWidth)) {
      cursor += block.lineHeight;
      if (draw) ctx.fillText(line, x, cursor - block.lineHeight * 0.28);
    }
  }
  return cursor - y;
}

function headerBlocks(chart: Omit<ChartExport, 'dataUrl'>, scale: number, stamp: string): TextBlock[] {
  const muted = SURFACE.mutedForeground;
  const blocks: TextBlock[] = [
    { text: BRAND, font: `500 ${11 * scale}px ${FONT}`, color: muted, lineHeight: 16 * scale },
    {
      text: chart.title,
      font: `600 ${18 * scale}px ${FONT}`,
      color: SURFACE.foreground,
      lineHeight: 26 * scale,
    },
  ];
  if (chart.subtitle) {
    blocks.push({
      text: chart.subtitle,
      font: `400 ${12 * scale}px ${FONT}`,
      color: SURFACE.foreground,
      lineHeight: 18 * scale,
    });
  }
  for (const line of chart.caption ?? []) {
    blocks.push({ text: line, font: `400 ${11 * scale}px ${FONT}`, color: muted, lineHeight: 16 * scale });
  }
  blocks.push({ text: stamp, font: `400 ${11 * scale}px ${FONT}`, color: muted, lineHeight: 16 * scale });
  return blocks;
}

function newCanvas(width: number, height: number): [HTMLCanvasElement, CanvasRenderingContext2D] {
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('Браузер не поддерживает canvas');
  ctx.fillStyle = SURFACE.card;
  ctx.fillRect(0, 0, width, height);
  return [canvas, ctx];
}

function download(href: string, fileName: string): void {
  const anchor = document.createElement('a');
  anchor.href = href;
  anchor.download = fileName;
  anchor.click();
}

/** Имя файла: латиница/кириллица и дата, без символов, запрещённых в ФС. */
export function exportFileName(base: string, ext: 'png' | 'pdf', date = new Date()): string {
  const day = date.toISOString().slice(0, 10);
  const safe = base.replace(/[\\/:*?"<>|]+/g, ' ').replace(/\s+/g, '-').slice(0, 60);
  return `${safe}_${day}.${ext}`;
}

/** PNG одного графика с шапкой (название, период, фильтры, дата формирования). */
export async function downloadChartPng(chart: ChartExport, fileBase: string): Promise<void> {
  const img = await loadImage(chart.dataUrl, chart.title);
  const scale = 2; // getDataURL отдаёт pixelRatio 2
  const padding = 24 * scale;
  const width = Math.max(img.width + padding * 2, 720 * scale);
  const contentWidth = width - padding * 2;
  const stamp = generatedStamp();
  const blocks = headerBlocks(chart, scale, stamp);
  const [, probe] = newCanvas(1, 1);
  const headerHeight = drawBlocks(probe, blocks, padding, padding, contentWidth, false);
  const height = padding + headerHeight + 12 * scale + img.height + padding;
  const [canvas, ctx] = newCanvas(width, height);
  drawBlocks(ctx, blocks, padding, padding, contentWidth, true);
  ctx.drawImage(img, padding + (contentWidth - img.width) / 2, padding + headerHeight + 12 * scale);
  download(canvas.toDataURL('image/png'), exportFileName(fileBase, 'png'));
}

// --- PDF ---------------------------------------------------------------------------------

interface JpegPage {
  bytes: Uint8Array;
  width: number;
  height: number;
}

function dataUrlBytes(dataUrl: string): Uint8Array {
  const base64 = dataUrl.slice(dataUrl.indexOf(',') + 1);
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

/**
 * Минимальный PDF 1.4: страница = одно JPEG-изображение на весь лист A4 (альбом).
 * Весь текст уже нарисован в растре, поэтому шрифты в PDF не нужны (кириллица
 * без встраивания шрифта). Смещения xref считаются в байтах.
 */
export function buildImagePdf(pages: JpegPage[]): Blob {
  const encoder = new TextEncoder();
  const chunks: Uint8Array[] = [];
  const offsets: number[] = [];
  let length = 0;
  const push = (part: string | Uint8Array): void => {
    const bytes = typeof part === 'string' ? encoder.encode(part) : part;
    chunks.push(bytes);
    length += bytes.length;
  };
  const beginObject = (id: number): void => {
    offsets[id] = length;
    push(`${id} 0 obj\n`);
  };

  // 1 — каталог, 2 — дерево страниц, далее на страницу по 3 объекта: page, content, image
  const pageIds = pages.map((_, index) => 3 + index * 3);
  push('%PDF-1.4\n%\xE2\xE3\xCF\xD3\n');
  beginObject(1);
  push('<< /Type /Catalog /Pages 2 0 R >>\nendobj\n');
  beginObject(2);
  push(`<< /Type /Pages /Kids [${pageIds.map((id) => `${id} 0 R`).join(' ')}] /Count ${pages.length} >>\nendobj\n`);
  pages.forEach((page, index) => {
    const pageId = pageIds[index]!;
    const contentId = pageId + 1;
    const imageId = pageId + 2;
    beginObject(pageId);
    push(
      `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${PAGE_W_PT} ${PAGE_H_PT}] ` +
        `/Resources << /XObject << /Im${index} ${imageId} 0 R >> >> /Contents ${contentId} 0 R >>\nendobj\n`,
    );
    const content = `q ${PAGE_W_PT} 0 0 ${PAGE_H_PT} 0 0 cm /Im${index} Do Q\n`;
    beginObject(contentId);
    push(`<< /Length ${encoder.encode(content).length} >>\nstream\n${content}endstream\nendobj\n`);
    beginObject(imageId);
    push(
      `<< /Type /XObject /Subtype /Image /Width ${page.width} /Height ${page.height} ` +
        `/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${page.bytes.length} >>\nstream\n`,
    );
    push(page.bytes);
    push('\nendstream\nendobj\n');
  });
  const total = 3 + pages.length * 3;
  const xref = length;
  push(`xref\n0 ${total}\n0000000000 65535 f \n`);
  for (let id = 1; id < total; id += 1) {
    push(`${String(offsets[id] ?? 0).padStart(10, '0')} 00000 n \n`);
  }
  push(`trailer\n<< /Size ${total} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
  return new Blob(chunks as BlobPart[], { type: 'application/pdf' });
}

function pageFooter(ctx: CanvasRenderingContext2D, page: number, pages: number, stamp: string): void {
  const s = PAGE_SCALE;
  ctx.font = `400 ${9 * s}px ${FONT}`;
  ctx.fillStyle = SURFACE.mutedForeground;
  ctx.fillText(`${BRAND} · ${stamp}`, 32 * s, (PAGE_H_PT - 18) * s);
  const label = `Стр. ${page} из ${pages}`;
  ctx.fillText(label, (PAGE_W_PT - 32) * s - ctx.measureText(label).width, (PAGE_H_PT - 18) * s);
}

function coverCanvas(cover: CoverPage, pages: number, stamp: string): HTMLCanvasElement {
  const s = PAGE_SCALE;
  const [canvas, ctx] = newCanvas(PAGE_W_PT * s, PAGE_H_PT * s);
  const x = 40 * s;
  const width = (PAGE_W_PT - 80) * s;
  let y = 48 * s;
  const blocks: TextBlock[] = [
    { text: BRAND, font: `500 ${12 * s}px ${FONT}`, color: SURFACE.mutedForeground, lineHeight: 18 * s },
    { text: cover.title, font: `600 ${24 * s}px ${FONT}`, color: SURFACE.foreground, lineHeight: 34 * s },
    ...cover.lines.map((line) => ({
      text: line,
      font: `400 ${12 * s}px ${FONT}`,
      color: SURFACE.foreground,
      lineHeight: 18 * s,
    })),
  ];
  y += drawBlocks(ctx, blocks, x, y, width, true) + 20 * s;
  // KPI плиткой: по 3 в ряд
  const columns = 3;
  const gap = 12 * s;
  const tileW = (width - gap * (columns - 1)) / columns;
  const tileH = 64 * s;
  cover.facts.forEach((fact, index) => {
    const col = index % columns;
    const row = Math.floor(index / columns);
    const tx = x + col * (tileW + gap);
    const ty = y + row * (tileH + gap);
    ctx.fillStyle = SURFACE.muted;
    ctx.fillRect(tx, ty, tileW, tileH);
    ctx.fillStyle = SURFACE.mutedForeground;
    ctx.font = `500 ${11 * s}px ${FONT}`;
    ctx.fillText(fact.label, tx + 12 * s, ty + 22 * s);
    ctx.fillStyle = SURFACE.foreground;
    ctx.font = `600 ${20 * s}px ${FONT}`;
    ctx.fillText(fact.value, tx + 12 * s, ty + 50 * s);
  });
  pageFooter(ctx, 1, pages, stamp);
  return canvas;
}

function chartCanvas(
  chart: ChartExport,
  img: HTMLImageElement,
  page: number,
  pages: number,
  stamp: string,
): HTMLCanvasElement {
  const s = PAGE_SCALE;
  const [canvas, ctx] = newCanvas(PAGE_W_PT * s, PAGE_H_PT * s);
  const x = 32 * s;
  const width = (PAGE_W_PT - 64) * s;
  const top = 28 * s;
  const headerHeight = drawBlocks(ctx, headerBlocks(chart, s * 0.9, stamp), x, top, width, true);
  const areaTop = top + headerHeight + 12 * s;
  const areaBottom = (PAGE_H_PT - 36) * s;
  const ratio = Math.min(width / img.width, (areaBottom - areaTop) / img.height);
  const drawW = img.width * ratio;
  const drawH = img.height * ratio;
  ctx.drawImage(img, x + (width - drawW) / 2, areaTop, drawW, drawH);
  pageFooter(ctx, page, pages, stamp);
  return canvas;
}

/** PDF всей панели: титул (фильтры, KPI) и по графику на страницу. */
export async function downloadDashboardPdf(
  cover: CoverPage,
  charts: ChartExport[],
  fileBase: string,
): Promise<void> {
  const stamp = generatedStamp();
  const images = await Promise.all(charts.map((chart) => loadImage(chart.dataUrl, chart.title)));
  const total = charts.length + 1;
  const canvases = [
    coverCanvas(cover, total, stamp),
    ...charts.map((chart, index) => chartCanvas(chart, images[index]!, index + 2, total, stamp)),
  ];
  const pages = canvases.map((canvas) => ({
    bytes: dataUrlBytes(canvas.toDataURL('image/jpeg', 0.92)),
    width: canvas.width,
    height: canvas.height,
  }));
  const url = URL.createObjectURL(buildImagePdf(pages));
  try {
    download(url, exportFileName(fileBase, 'pdf'));
  } finally {
    // даём браузеру начать скачивание до освобождения ссылки
    window.setTimeout(() => URL.revokeObjectURL(url), 4_000);
  }
}

/**
 * Композитный PNG всего дашборда (ux.md §6.2): шапка с периодом и фильтрами, ниже
 * графики друг под другом, у каждого — название и подзаголовок.
 */
export async function downloadDashboardPng(
  charts: ChartExport[],
  caption: string[],
  fileBase: string,
): Promise<void> {
  const WIDTH = 1200;
  const PADDING = 24;
  const contentWidth = WIDTH - PADDING * 2;
  const stamp = generatedStamp();
  const images = await Promise.all(charts.map((chart) => loadImage(chart.dataUrl, chart.title)));
  const [, probe] = newCanvas(1, 1);
  const head = headerBlocks({ title: 'Панель аналитики', caption }, 1.3, stamp);
  const headHeight = drawBlocks(probe, head, PADDING, PADDING, contentWidth, false);
  const sections = charts.map((chart, index) => {
    const img = images[index]!;
    const blocks: TextBlock[] = [
      { text: chart.title, font: `600 18px ${FONT}`, color: SURFACE.foreground, lineHeight: 26 },
    ];
    if (chart.subtitle) {
      blocks.push({
        text: chart.subtitle,
        font: `400 14px ${FONT}`,
        color: SURFACE.mutedForeground,
        lineHeight: 20,
      });
    }
    const titleHeight = drawBlocks(probe, blocks, PADDING, 0, contentWidth, false);
    const height = Math.round(img.height * (contentWidth / img.width));
    return { img, blocks, titleHeight, height };
  });
  const totalHeight =
    PADDING +
    headHeight +
    sections.reduce((sum, section) => sum + PADDING + section.titleHeight + 8 + section.height, 0) +
    PADDING;
  const [canvas, ctx] = newCanvas(WIDTH, totalHeight);
  let y = PADDING + drawBlocks(ctx, head, PADDING, PADDING, contentWidth, true);
  for (const section of sections) {
    y += PADDING;
    y += drawBlocks(ctx, section.blocks, PADDING, y, contentWidth, true) + 8;
    ctx.drawImage(section.img, PADDING, y, contentWidth, section.height);
    y += section.height;
  }
  download(canvas.toDataURL('image/png'), exportFileName(fileBase, 'png'));
}
