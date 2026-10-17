import type { ImportSession } from '@/shared/api/types';

/**
 * Клиентская часть мастера импорта (ux.md §11): словари сущностей,
 * парсер CSV для мгновенного превью при смене кодировки (файл ещё в памяти
 * браузера — серверу для перерисовки ходить не обязательно) и эвристика
 * «кракозябр».
 */

export interface EntityOption {
  value: string;
  label: string;
  /** Куда ведёт кнопка «Открыть реестр» после импорта. */
  registryPath: string;
}

/** Только то, что принимает сервер (import_export/mapping.py `_ENTITY_REGISTRY` + справочники). */
export const ENTITY_OPTIONS: EntityOption[] = [
  { value: 'universities', label: 'Вузы', registryPath: '/registry/universities' },
  { value: 'requests_b2b', label: 'Заявки B2B (вузы)', registryPath: '/board?wf=b2b' },
  { value: 'requests_b2c', label: 'Заявки B2C (физлица и юрлица)', registryPath: '/board?wf=b2c' },
  {
    value: 'kam_catalog',
    label: 'Таблица КАМов: вузы, ПО, договоры, лицензии',
    registryPath: '/registry/contracts',
  },
  { value: 'students', label: 'Студенты (Пул талантов)', registryPath: '/talent-pool' },
  { value: 'dictionary:products', label: 'Справочник: продукты', registryPath: '/admin/dictionaries' },
];

// ---------------------------------------------------------------------------
// Памятка по составному импорту и шаблон файла (шаг 1 мастера)
// ---------------------------------------------------------------------------

export interface EntityGuideColumn {
  /** Заголовок колонки в шаблоне — сервер узнаёт его и синонимы автоматически. */
  name: string;
  required?: boolean;
  hint: string;
}

export interface EntityGuide {
  summary: string;
  columns: EntityGuideColumn[];
  /** Синтетические строки-примеры для шаблона. */
  example: string[][];
  templateName: string;
}

/**
 * Таблица КАМов (import_export/mapping.py `KAM_CATALOG_FIELDS`): строка — лицензия на
 * ИТ-продукт по договору с вузом. Договор ищется по номеру (обновляется или создаётся),
 * вуз и продукт — по справочникам, КАМ — по «Фамилия Имя».
 */
const KAM_CATALOG_GUIDE: EntityGuide = {
  summary:
    'Одна строка — лицензия на ИТ-продукт по договору с вузом. Договор ищется по номеру: ' +
    'есть — обновится, нет — создастся. Несколько продуктов одного договора — отдельными ' +
    'строками или через запятую. Объединённые ячейки Excel (вуз на несколько строк) читаются.',
  columns: [
    { name: 'Вуз', required: true, hint: 'название или ИНН; новый вуз — правило «Создавать отсутствующие»' },
    { name: 'ИТ-направление', hint: 'из справочника, несколько — через запятую' },
    { name: 'Вендор', hint: 'компания-правообладатель ПО' },
    { name: 'ИТ-продукт (ПО)', required: true, hint: 'код или название из каталога продуктов' },
    { name: 'Номер договора', required: true, hint: 'ключ строки: договор обновляется по номеру' },
    { name: 'Дата подписания лицензии', hint: 'дд.мм.гггг' },
    { name: 'Срок лицензии, лет', hint: 'число лет, год окончания (2027) или «бессрочно»' },
    {
      name: 'Статус передачи лицензии',
      hint: 'не передана · в процессе передачи · передана · отменена',
    },
    { name: 'Ответственный КАМ', hint: 'Фамилия Имя сотрудника' },
    { name: 'Ответственные от вуза', hint: 'ФИО (должность), телефон, почта; люди — через «;»' },
    { name: 'Комментарий', hint: 'попадёт в строку договора' },
  ],
  example: [
    [
      'Северный технологический университет',
      'Анализ данных',
      'ООО «Пример Софт»',
      'Пример.Аналитика',
      'ЛЦ-2026/001',
      '01.02.2026',
      '1',
      'Передана',
      'Петров Константин',
      'Смирнова Анна Олеговна (проректор), +7 900 000-00-01',
      'Пилот на кафедре ИТ',
    ],
    [
      'Северный технологический университет',
      'DevOps',
      'ООО «Пример Софт»',
      'Пример.Конвейер',
      'ЛЦ-2026/001',
      '01.02.2026',
      '2028',
      'В процессе передачи',
      'Петров Константин',
      '',
      '',
    ],
  ],
  templateName: 'shablon-tablitsa-kamov.csv',
};

const ENTITY_GUIDES: Record<string, EntityGuide> = {
  kam_catalog: KAM_CATALOG_GUIDE,
};

export function entityGuide(value: string): EntityGuide | null {
  return ENTITY_GUIDES[value] ?? null;
}

function csvCell(value: string): string {
  return /[";\n\r]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

/**
 * CSV-шаблон для Excel: UTF-8 с BOM и разделителем «;» — Excel в русской локали
 * открывает его без мастера, а импорт определяет кодировку и разделитель сам.
 */
export function templateCsv(guide: EntityGuide): string {
  const lines = [guide.columns.map((c) => c.name), ...guide.example].map((row) =>
    row.map(csvCell).join(';'),
  );
  return `\uFEFF${lines.join('\r\n')}\r\n`;
}

export function downloadTemplate(guide: EntityGuide): void {
  const blob = new Blob([templateCsv(guide)], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = guide.templateName;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  }
}

/** Целевая сущность-справочник админки: `dictionary:<name>` (api-contract §10.1). */
export function isDictionaryEntity(value: string): boolean {
  return value.startsWith('dictionary:');
}

export function entityLabel(value: string): string {
  if (isDictionaryEntity(value)) {
    return `Справочник: ${value.slice('dictionary:'.length)}`;
  }
  return ENTITY_OPTIONS.find((o) => o.value === value)?.label ?? value;
}

export function registryPathFor(value: string): string {
  if (isDictionaryEntity(value)) return '/admin/dictionaries';
  return ENTITY_OPTIONS.find((o) => o.value === value)?.registryPath ?? '/dashboard';
}

export const MAX_FILE_SIZE_MB = 20; // = backend files.service.MAX_IMPORT_BYTES
export const ACCEPTED_EXTENSIONS = ['xlsx', 'xls', 'csv', 'tsv', 'json'];

export function fileExtension(name: string): string {
  return name.split('.').pop()?.toLowerCase() ?? '';
}

export type FileKind = 'csv' | 'json' | 'excel';

export function fileKind(name: string): FileKind {
  const ext = fileExtension(name);
  if (ext === 'csv' || ext === 'tsv') return 'csv';
  if (ext === 'json') return 'json';
  return 'excel';
}

// ---------------------------------------------------------------------------
// Кодировки (api-contract.md §6.2: utf-8-sig → utf-8 → cp1251 → koi8-r → cp866)
// ---------------------------------------------------------------------------

export interface EncodingOption {
  /** Значение для options.encoding на сервере (null — авто-детект). */
  value: string | null;
  label: string;
  /** Метка для TextDecoder браузера. */
  decoder: string;
}

export const ENCODING_OPTIONS: EncodingOption[] = [
  { value: null, label: 'Авто', decoder: 'utf-8' },
  { value: 'utf-8', label: 'UTF-8', decoder: 'utf-8' },
  { value: 'cp1251', label: 'Windows-1251', decoder: 'windows-1251' },
  { value: 'koi8-r', label: 'KOI8-R', decoder: 'koi8-r' },
  { value: 'cp866', label: 'CP866 (DOS)', decoder: 'ibm866' },
];

/** Метка TextDecoder для значения кодировки (серверного или детектированного). */
export function decoderLabelFor(encoding: string | null | undefined): string {
  if (!encoding) return 'utf-8';
  const normalized = encoding.toLowerCase().replace(/[^a-z0-9]/g, '');
  if (normalized.includes('1251')) return 'windows-1251';
  if (normalized.includes('koi8')) return 'koi8-r';
  if (normalized.includes('866')) return 'ibm866';
  return 'utf-8';
}

export function decodeBuffer(buffer: ArrayBuffer, decoderLabel: string): string {
  try {
    return new TextDecoder(decoderLabel).decode(buffer);
  } catch {
    return new TextDecoder('utf-8').decode(buffer);
  }
}

// ---------------------------------------------------------------------------
// CSV-парсер превью (RFC 4180-подобный: кавычки, экранирование "")
// ---------------------------------------------------------------------------

function sniffDelimiter(firstLine: string): string {
  const candidates = [';', ',', '\t', '|'];
  let best = ',';
  let bestCount = 0;
  for (const candidate of candidates) {
    const count = firstLine.split(candidate).length - 1;
    if (count > bestCount) {
      best = candidate;
      bestCount = count;
    }
  }
  return best;
}

export function parseCsv(text: string, maxRows: number): string[][] {
  const clean = text.replace(/^﻿/, '');
  const firstLineEnd = clean.search(/\r?\n/);
  const delimiter = sniffDelimiter(firstLineEnd === -1 ? clean : clean.slice(0, firstLineEnd));
  const rows: string[][] = [];
  let row: string[] = [];
  let field = '';
  let inQuotes = false;
  for (let i = 0; i < clean.length; i += 1) {
    const ch = clean[i];
    if (inQuotes) {
      if (ch === '"') {
        if (clean[i + 1] === '"') {
          field += '"';
          i += 1;
        } else {
          inQuotes = false;
        }
      } else {
        field += ch;
      }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === delimiter) {
      row.push(field);
      field = '';
    } else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && clean[i + 1] === '\n') i += 1;
      row.push(field);
      field = '';
      if (row.some((cell) => cell.trim() !== '')) rows.push(row);
      row = [];
      if (rows.length >= maxRows) return rows;
    } else {
      field += ch;
    }
  }
  row.push(field);
  if (row.some((cell) => cell.trim() !== '')) rows.push(row);
  return rows.slice(0, maxRows);
}

// ---------------------------------------------------------------------------
// Превью и эвристика кракозябр
// ---------------------------------------------------------------------------

export interface PreviewTable {
  header: string[];
  rows: string[][];
}

const PREVIEW_ROWS = 20;

/**
 * Таблица-превью шага 2: для CSV/JSON файл ещё в памяти — декодируем и парсим
 * на клиенте (кодировка меняется мгновенно); для Excel и после F5 — данные
 * сервера (`preview_rows`, а без них — транспонированные samples колонок).
 */
export function buildPreview(
  session: ImportSession,
  buffer: ArrayBuffer | null,
  kind: FileKind | null,
  encoding: string | null,
  headerRow: boolean,
): PreviewTable | null {
  if (buffer && kind === 'csv') {
    const decoder = encoding ? decoderLabelFor(encoding) : decoderLabelFor(session.detected?.encoding);
    const parsed = parseCsv(decodeBuffer(buffer, decoder), PREVIEW_ROWS + 1);
    if (parsed.length === 0) return null;
    const width = Math.max(...parsed.map((r) => r.length));
    const pad = (r: string[]): string[] =>
      [...r, ...Array<string>(Math.max(0, width - r.length)).fill('')];
    if (headerRow) {
      return { header: pad(parsed[0]), rows: parsed.slice(1, PREVIEW_ROWS + 1).map(pad) };
    }
    return {
      header: Array.from({ length: width }, (_v, i) => `Колонка ${i + 1}`),
      rows: parsed.slice(0, PREVIEW_ROWS).map(pad),
    };
  }
  if (buffer && kind === 'json') {
    try {
      const data = JSON.parse(decodeBuffer(buffer, 'utf-8')) as unknown;
      if (Array.isArray(data) && data.length > 0) {
        const keys = [...new Set(data.slice(0, PREVIEW_ROWS).flatMap((item) => Object.keys(item as object)))];
        return {
          header: keys,
          rows: data.slice(0, PREVIEW_ROWS).map((item) =>
            keys.map((key) => {
              const value = (item as Record<string, unknown>)[key];
              return value === null || value === undefined ? '' : String(value);
            }),
          ),
        };
      }
    } catch {
      return null;
    }
  }
  const detected = session.detected;
  if (!detected) return null;
  const header = detected.columns.map((c) => c.name);
  if (detected.preview_rows && detected.preview_rows.length > 0) {
    return { header, rows: detected.preview_rows.slice(0, PREVIEW_ROWS) };
  }
  const sampleCount = Math.max(0, ...detected.columns.map((c) => c.samples.length));
  const rows = Array.from({ length: sampleCount }, (_v, rowIndex) =>
    detected.columns.map((c) => c.samples[rowIndex] ?? ''),
  );
  return { header, rows };
}

/**
 * Эвристика «кодировка выбрана неверно» (ux.md §11.2): много символов
 * замены/типичных артефактов перекодировки либо не-ASCII текст почти
 * без кириллицы.
 */
export function looksMojibake(preview: PreviewTable | null): boolean {
  if (!preview) return false;
  const text = [preview.header.join(' '), ...preview.rows.slice(0, 5).map((r) => r.join(' '))].join(' ');
  if (text.length === 0) return false;
  const replacement = (text.match(/�/g) ?? []).length;
  if (replacement / text.length > 0.02) return true;
  const artifacts = (text.match(/[ÐÑÂÃ¡-ÿ]/g) ?? []).length;
  const cyrillic = (text.match(/[А-Яа-яЁё]/g) ?? []).length;
  const nonAscii = (text.match(/[^\x20-\x7E\s]/g) ?? []).length;
  if (nonAscii < 5) return false;
  return cyrillic / nonAscii < 0.3 && artifacts / nonAscii > 0.3;
}

// ---------------------------------------------------------------------------
// Преобразования и правила-справочники (api-contract.md §6.3)
// ---------------------------------------------------------------------------

export const TRANSFORM_OPTIONS = [
  { value: '', label: 'Как есть' },
  { value: 'trim', label: 'Обрезать пробелы' },
  { value: 'lowercase', label: 'В нижний регистр' },
  { value: 'uppercase', label: 'В верхний регистр' },
  { value: 'date_ru', label: 'Дата дд.мм.гггг → ISO' },
  { value: 'phone_normalize', label: 'Нормализовать телефон' },
];

interface LookupOption {
  value: string;
  label: string;
}

/** Поля-справочники: как искать значение в CRM (ux.md §11.3 «правило»). */
const LOOKUP_RULES: Record<string, LookupOption[]> = {
  university: [
    { value: 'by_name_or_inn', label: 'Искать по названию или ИНН' },
    { value: 'match_or_create', label: 'Создавать отсутствующие' },
    { value: 'strict', label: 'Ошибка, если не найден' },
  ],
  program: [
    { value: 'by_code_or_name', label: 'Искать по коду или названию' },
    { value: 'match_or_create', label: 'Создавать отсутствующие' },
    { value: 'strict', label: 'Ошибка, если не найдена' },
  ],
  product: [
    { value: 'by_code_or_name', label: 'Искать по коду или названию' },
    { value: 'match_or_create', label: 'Создавать отсутствующие' },
    { value: 'strict', label: 'Ошибка, если не найден' },
  ],
  region: [
    { value: 'by_name', label: 'Искать по названию' },
    { value: 'match_or_create', label: 'Создавать отсутствующие' },
    { value: 'strict', label: 'Ошибка, если не найден' },
  ],
  federal_project: [
    { value: 'by_name', label: 'Искать по названию' },
    { value: 'match_or_create', label: 'Создавать отсутствующие' },
  ],
};

/** Поля, которые ищутся в справочнике без создания: правило показывается подсказкой. */
const LOOKUP_HINTS: Record<string, string> = {
  it_direction: 'Ищется в справочнике ИТ-направлений по коду или названию',
  kam: 'Сотрудник по «Фамилия Имя», ФИО, логину или email',
};

export function lookupHintFor(field: string): string | null {
  return LOOKUP_HINTS[field] ?? null;
}

export function lookupOptionsFor(field: string): LookupOption[] | null {
  return LOOKUP_RULES[field] ?? null;
}

/** ПДн-поля: бейдж «будет зашифровано» (ux.md §11.3, шифрование на бэкенде). */
export function isPiiField(field: string): boolean {
  return ['full_name', 'email', 'phone'].includes(field);
}
