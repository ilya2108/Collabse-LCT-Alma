/**
 * Модель встроенной справки (НФТ «документация встроена в платформу»).
 *
 * Один источник на две выдачи: экран /help в SPA (HelpPage) и печатные
 * руководства docs/manual/*.html + PDF (генератор docs/manual/build-manual.ts).
 * Поэтому здесь только данные без React и без алиасов `@/` — файл читает и Vite,
 * и `node --experimental-strip-types`.
 *
 * Строчная разметка текста (`Inline`): **жирный**, `код`, [ссылка](/путь|#раздел|https://…).
 */

/** Текст с мини-разметкой: **жирный**, `код`, [текст](адрес). */
export type Inline = string;

export type HelpBlock =
  | { kind: 'p'; text: Inline; lead?: boolean }
  | { kind: 'h3'; text: string }
  | { kind: 'steps'; items: Inline[] }
  | { kind: 'list'; items: Inline[] }
  | { kind: 'table'; head: string[]; rows: Inline[][] }
  | { kind: 'callout'; tone?: 'info' | 'warn'; title?: string; text: Inline }
  | { kind: 'flow'; steps: Array<{ title: string; text: Inline }> }
  | { kind: 'code'; text: string }
  | {
      kind: 'figure';
      /** Имя файла снимка: в SPA — /help/img/<img>, в печати — docs/manual/img/<img>. */
      img: string;
      alt: string;
      caption: string;
      /** Снимок с телефона — узкая колонка. */
      mobile?: boolean;
    }
  | { kind: 'faq'; q: string; a: Inline[] };

export interface HelpSection {
  /** Якорь раздела: /help#<id>. Стабилен — на него ссылаются экраны. */
  id: string;
  title: string;
  /** Одна строка под заголовком в оглавлении. */
  summary: string;
  /** Только в приложении или только в печатной версии (данные демо-стенда). */
  only?: 'app' | 'print';
  blocks: HelpBlock[];
}

export interface HelpGuide {
  id: 'user' | 'admin';
  title: string;
  /** Подзаголовок обложки печатной версии. */
  subtitle: string;
  version: string;
  /** Дата актуализации — ДД.ММ.ГГГГ. */
  updated: string;
  sections: HelpSection[];
}

export type InlineToken =
  | { t: 'text'; text: string }
  | { t: 'b'; text: string }
  | { t: 'code'; text: string }
  | { t: 'link'; text: string; href: string };

const INLINE_RE = /\*\*(.+?)\*\*|`([^`]+)`|\[([^\]]+)\]\(([^)\s]+)\)/g;

/** Разбор мини-разметки в токены — общий для React-рендера и генератора печати. */
export function parseInline(source: Inline): InlineToken[] {
  const tokens: InlineToken[] = [];
  let last = 0;
  for (const match of source.matchAll(INLINE_RE)) {
    const index = match.index ?? 0;
    if (index > last) tokens.push({ t: 'text', text: source.slice(last, index) });
    if (match[1] !== undefined) tokens.push({ t: 'b', text: match[1] });
    else if (match[2] !== undefined) tokens.push({ t: 'code', text: match[2] });
    else tokens.push({ t: 'link', text: match[3] ?? '', href: match[4] ?? '' });
    last = index + match[0].length;
  }
  if (last < source.length) tokens.push({ t: 'text', text: source.slice(last) });
  return tokens;
}

/** Плоский текст блока — для поиска по справке. */
export function blockText(block: HelpBlock): string {
  switch (block.kind) {
    case 'p':
    case 'h3':
    case 'code':
      return block.text;
    case 'steps':
    case 'list':
      return block.items.join(' ');
    case 'table':
      return [...block.head, ...block.rows.flat()].join(' ');
    case 'callout':
      return `${block.title ?? ''} ${block.text}`;
    case 'flow':
      return block.steps.map((step) => `${step.title} ${step.text}`).join(' ');
    case 'figure':
      return block.caption;
    case 'faq':
      return `${block.q} ${block.a.join(' ')}`;
  }
}

/** Разделы для выдачи: в приложении без `only: 'print'`, в печати без `only: 'app'`. */
export function sectionsFor(guide: HelpGuide, target: 'app' | 'print'): HelpSection[] {
  return guide.sections.filter((section) => !section.only || section.only === target);
}
