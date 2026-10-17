/**
 * Сборка печатных руководств из текста встроенной справки.
 *
 * Источник один — frontend/src/pages/help/content/*.ts: его показывает экран /help
 * в приложении, а этот скрипт делает из него HTML для печати и PDF. Править текст —
 * только там; manual.html и admin-guide.html здесь генерируются.
 *
 *   node --experimental-strip-types docs/manual/build-manual.ts          # HTML + проверки
 *   node --experimental-strip-types docs/manual/build-manual.ts --check  # только проверки
 *   node --experimental-strip-types docs/manual/build-manual.ts --pdf    # HTML + PDF (Chrome)
 *
 * Проверки (ненулевой код выхода при нарушении): снимки есть в docs/manual/img и
 * frontend/public/help/img, якоря #раздел существуют, ссылки ведут на маршруты SPA,
 * в тексте нет утверждений, которые код уже опроверг.
 */
import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  HELP_GUIDES,
  parseInline,
  sectionsFor,
  type HelpBlock,
  type HelpGuide,
  type Inline,
} from '../../frontend/src/pages/help/content/index.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, '..', '..');
const DOCS_IMG = join(HERE, 'img');
const APP_IMG = join(ROOT, 'frontend', 'public', 'help', 'img');
const ROUTER = join(ROOT, 'frontend', 'src', 'app', 'router', 'index.tsx');

const OUTPUTS: Record<HelpGuide['id'], { html: string; pdf: string }> = {
  user: { html: 'manual.html', pdf: 'Альма — руководство пользователя.pdf' },
  admin: { html: 'admin-guide.html', pdf: 'Альма — руководство администратора.pdf' },
};

/** Утверждения, которые код опроверг (P1-9): импорта договоров и платежей нет, UI правил уведомлений нет. */
const FORBIDDEN: Array<[RegExp, string]> = [
  [/платеж|платёж/i, 'импорта платежей нет'],
  [/Профиль\s*→\s*Уведомления/, 'пункта «Профиль → Уведомления» нет: «Нотификации» / «Настройки нотификаций»'],
  [/Администрирование\s*→\s*Правила уведомлений/, 'экрана правил уведомлений нет — только API'],
  [/Канбан заявок|Talent pool|Конструктор workflow|Дашборд/, 'старые названия разделов меню'],
  [/мобильная версия не нужна|адаптив до планшета/i, 'единая формулировка: адаптивный веб-интерфейс от 375 px'],
];

// --- HTML ---------------------------------------------------------------------------

function esc(value: string): string {
  return value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
}

function inline(text: Inline): string {
  return parseInline(text)
    .map((token) => {
      switch (token.t) {
        case 'b':
          return `<b>${esc(token.text)}</b>`;
        case 'code':
          return `<code>${esc(token.text)}</code>`;
        case 'link':
          if (token.href.startsWith('#')) return `<a href="#sec-${esc(token.href.slice(1))}">${esc(token.text)}</a>`;
          if (token.href.startsWith('/api/')) return `<code>${esc(token.href)}</code>`;
          // маршрут приложения на бумаге — просто название раздела
          if (token.href.startsWith('/')) return `<b>${esc(token.text)}</b>`;
          return `<a href="${esc(token.href)}">${esc(token.text)}</a>`;
        default:
          return esc(token.text);
      }
    })
    .join('');
}

function block(item: HelpBlock): string {
  switch (item.kind) {
    case 'p':
      return `<p${item.lead ? ' class="lead"' : ''}>${inline(item.text)}</p>`;
    case 'h3':
      return `<h3>${esc(item.text)}</h3>`;
    case 'steps':
      return `<ol class="steps">${item.items.map((li) => `<li>${inline(li)}</li>`).join('')}</ol>`;
    case 'list':
      return `<ul class="dots">${item.items.map((li) => `<li>${inline(li)}</li>`).join('')}</ul>`;
    case 'table':
      return `<table class="tbl"><thead><tr>${item.head.map((h) => `<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${item.rows
        .map((row) => `<tr>${row.map((cell) => `<td>${inline(cell)}</td>`).join('')}</tr>`)
        .join('')}</tbody></table>`;
    case 'callout':
      return `<div class="callout${item.tone === 'warn' ? ' warn' : ''}">${item.title ? `<b>${esc(item.title)}.</b> ` : ''}${inline(item.text)}</div>`;
    case 'flow':
      return `<div class="flow">${item.steps
        .map((step, i) => `<div class="fstep"><b>${i + 1} · ${esc(step.title)}</b>${inline(step.text)}</div>`)
        .join('')}</div>`;
    case 'code':
      return `<pre class="cmd">${esc(item.text)}</pre>`;
    case 'figure':
      return `<figure class="shot${item.mobile ? ' mobile' : ''}"><img src="img/${esc(item.img)}" alt="${esc(item.alt)}"><figcaption>${esc(item.caption)}</figcaption></figure>`;
    case 'faq':
      return `<div class="panel"><h3>${esc(item.q)}</h3>${item.a.map((a) => `<p>${inline(a)}</p>`).join('')}</div>`;
  }
}

/** Подряд идущие снимки с телефона — одним рядом. */
function blocks(items: HelpBlock[]): string {
  const out: string[] = [];
  let row: string[] = [];
  const flush = (): void => {
    if (row.length) out.push(`<div class="shot-row">${row.join('')}</div>`);
    row = [];
  };
  for (const item of items) {
    if (item.kind === 'figure' && item.mobile) {
      row.push(block(item));
    } else {
      flush();
      out.push(block(item));
    }
  }
  flush();
  return out.join('\n');
}

const CSS = `
  :root { --blue: #1F69FF; --blue-dark: #1450C8; --blue-pale: #EDF3FF; --ink: #1c2333; --muted: #5b6577; --line: #d8e0ee; --card: #f7f9fd; }
  * { box-sizing: border-box; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  html, body { margin: 0; padding: 0; background: #fff; }
  body { font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", "Helvetica Neue", Arial, sans-serif; color: var(--ink); font-size: 10.2pt; line-height: 1.5; }
  @page { size: A4; margin: 20mm 16mm 18mm 16mm;
    @top-left { content: "Альма"; color: #1F69FF; font: 600 8pt -apple-system, "Segoe UI", Arial, sans-serif; }
    @top-right { content: "__RUNNING__"; color: #5b6577; font: 8pt -apple-system, "Segoe UI", Arial, sans-serif; }
    @bottom-right { content: counter(page); color: #1c2333; font: 600 8pt -apple-system, "Segoe UI", Arial, sans-serif; }
  }
  @page :first { @top-left { content: none; } @top-right { content: none; } @bottom-right { content: none; } }
  .wrap { max-width: 178mm; margin: 0 auto; }
  .cover { min-height: 250mm; display: flex; flex-direction: column; break-after: page; padding-top: 6mm; }
  .cover-band { height: 2.5mm; width: 34mm; background: var(--blue); border-radius: 2mm; margin-bottom: 10mm; }
  .cover h1 { font-size: 34pt; letter-spacing: -0.02em; line-height: 1.1; margin: 0; color: var(--blue); }
  .cover .subtitle { font-size: 15pt; color: var(--muted); margin-top: 3mm; }
  .cover .meta { margin-top: 7mm; font-size: 10pt; color: var(--muted); display: flex; gap: 8mm; flex-wrap: wrap; }
  .cover .meta b { color: var(--ink); }
  .cover figure { margin: 12mm 0 0; }
  .cover figure img { width: 100%; border: 0.4mm solid var(--line); border-radius: 3mm; }
  .cover .note { margin-top: 8mm; }
  .cover .cover-foot { margin-top: auto; display: flex; justify-content: space-between; font-size: 9pt; color: var(--muted); border-top: 0.4mm solid var(--line); padding-top: 3mm; }
  .toc { break-after: page; }
  .toc h2 { color: var(--blue); font-size: 17pt; margin: 0 0 4mm; }
  .toc ol { list-style: none; margin: 0; padding: 0; }
  .toc li { display: flex; gap: 3mm; padding: 1.6mm 0; border-bottom: 0.3mm dotted #c3cde0; }
  .toc .n { color: var(--blue); font-weight: 700; min-width: 7mm; }
  .toc a { color: var(--ink); text-decoration: none; font-weight: 600; }
  .toc .s { color: var(--muted); font-size: 9pt; display: block; font-weight: 400; }
  section.sec { margin-bottom: 8mm; }
  h2.section { color: var(--blue); font-size: 16pt; font-weight: 700; margin: 0 0 3.5mm; display: flex; align-items: center; gap: 3mm; break-after: avoid; }
  h2.section .num { background: var(--blue); color: #fff; border-radius: 2mm; font-size: 10.5pt; padding: 0.8mm 2.6mm; }
  h3 { color: var(--blue-dark); font-size: 10.5pt; margin: 3mm 0 1.2mm; break-after: avoid; }
  p { margin: 0 0 2.6mm; text-align: justify; hyphens: auto; }
  p.lead { color: #303a4f; }
  ol.steps { margin: 1.5mm 0 3mm 5.5mm; padding: 0; }
  ol.steps li { margin-bottom: 1.5mm; padding-left: 1mm; }
  ol.steps li::marker { color: var(--blue); font-weight: 700; }
  ul.dots { margin: 1.5mm 0 3mm 0; list-style: none; padding: 0; }
  ul.dots li { margin-bottom: 1.4mm; padding-left: 5mm; position: relative; }
  ul.dots li::before { content: ""; position: absolute; left: 0.6mm; top: 2.1mm; width: 1.7mm; height: 1.7mm; border-radius: 50%; background: var(--blue); }
  b { font-weight: 600; }
  a { color: var(--blue-dark); }
  code { font-family: "SF Mono", ui-monospace, Menlo, Consolas, monospace; font-size: 8.8pt; background: var(--blue-pale); border-radius: 1mm; padding: 0.2mm 1.2mm; color: var(--blue-dark); }
  pre.cmd { font-family: "SF Mono", ui-monospace, Menlo, Consolas, monospace; font-size: 8.4pt; line-height: 1.55; background: #10182b; color: #dce7ff; border-radius: 2mm; padding: 3.5mm 4mm; margin: 2mm 0 3mm; white-space: pre-wrap; word-break: break-all; break-inside: avoid; }
  figure.shot { margin: 3mm 0 2mm; break-inside: avoid; }
  figure.shot img { display: block; width: 100%; border: 0.35mm solid var(--line); border-radius: 2mm; }
  figure.shot figcaption { font-size: 8.3pt; color: var(--muted); margin-top: 1.6mm; text-align: center; }
  .shot-row { display: flex; gap: 6mm; justify-content: center; margin: 3mm 0 2mm; break-inside: avoid; }
  .shot-row figure.shot { margin: 0; width: 52mm; }
  .callout { background: var(--blue-pale); border-left: 1.2mm solid var(--blue); border-radius: 0 2mm 2mm 0; padding: 2.6mm 3.5mm; margin: 2.5mm 0; font-size: 9.4pt; break-inside: avoid; }
  .callout.warn { background: #fff7ec; border-left-color: #e8912d; }
  .panel { border: 0.35mm solid var(--line); border-radius: 2.5mm; background: var(--card); padding: 3mm 4mm; margin: 2.5mm 0; break-inside: avoid; }
  .panel h3 { margin-top: 0; }
  .panel p:last-child { margin-bottom: 0; }
  table.tbl { width: 100%; border-collapse: collapse; margin: 2.5mm 0 3mm; font-size: 9.2pt; break-inside: auto; }
  table.tbl th { background: var(--blue); color: #fff; text-align: left; font-weight: 600; padding: 2mm 2.8mm; font-size: 8.8pt; }
  table.tbl td { border-bottom: 0.3mm solid var(--line); padding: 1.8mm 2.8mm; vertical-align: top; }
  table.tbl tr { break-inside: avoid; }
  table.tbl tr:nth-child(even) td { background: #f6f9ff; }
  .flow { display: flex; align-items: stretch; gap: 2.5mm; margin: 3mm 0; break-inside: avoid; }
  .flow .fstep { flex: 1; background: #fff; border: 0.4mm solid var(--line); border-top: 1mm solid var(--blue); border-radius: 2mm; padding: 2.6mm 2.8mm; font-size: 8.8pt; line-height: 1.4; }
  .flow .fstep b { display: block; color: var(--blue-dark); margin-bottom: 0.8mm; font-size: 9.2pt; }
  @media screen { body { padding: 12mm 0; background: #eef1f6; } .wrap { background: #fff; padding: 16mm; box-shadow: 0 2mm 8mm rgba(0,0,0,.08); } }
`;

function render(guide: HelpGuide): string {
  const sections = sectionsFor(guide, 'print');
  const running = `Альма — ${guide.title.toLowerCase()}`;
  const cover = `
<div class="cover">
  <div class="cover-band"></div>
  <h1>Альма</h1>
  <div class="subtitle">${esc(guide.title)}: ${esc(guide.subtitle)}</div>
  <div class="meta">
    <span>Версия <b>${esc(guide.version)}</b></span>
    <span>Актуально на <b>${esc(guide.updated)}</b></span>
    <span>В приложении: <b>меню «Справка»${guide.id === 'admin' ? ' → «Администратору»' : ''}</b></span>
  </div>
  ${guide.id === 'user' ? '<figure><img src="img/app-login.png" alt="Экран входа в систему Альма"></figure>' : ''}
  <div class="callout note">Этот документ собран из встроенной справки «Альмы» (раздел «Справка» в меню приложения) и совпадает с ней по тексту.</div>
  <div class="cover-foot"><span>Команда Collabse · ЛЦТ 2026</span><span>${esc(running)}</span></div>
</div>`;
  const toc = `
<div class="toc">
  <h2>Оглавление</h2>
  <ol>${sections
    .map(
      (section, i) =>
        `<li><span class="n">${i + 1}</span><span><a href="#sec-${esc(section.id)}">${esc(section.title)}</a><span class="s">${esc(section.summary)}</span></span></li>`,
    )
    .join('')}</ol>
</div>`;
  const body = sections
    .map(
      (section, i) => `
<section class="sec" id="sec-${esc(section.id)}">
  <h2 class="section"><span class="num">${i + 1}</span>${esc(section.title)}</h2>
  ${blocks(section.blocks)}
</section>`,
    )
    .join('\n');
  return `<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<!-- Сгенерировано docs/manual/build-manual.ts из frontend/src/pages/help/content — не править вручную -->
<title>${esc(running)}</title>
<style>${CSS.replace('__RUNNING__', running)}</style>
</head>
<body>
<div class="wrap">
${cover}
${toc}
${body}
</div>
</body>
</html>
`;
}

// --- Проверки ---------------------------------------------------------------------------

function routePatterns(): string[] {
  const source = readFileSync(ROUTER, 'utf8');
  return [...source.matchAll(/path:\s*'([^']+)'/g)].map((m) => `/${m[1]}`.replace('//', '/'));
}

function routeExists(href: string, patterns: string[]): boolean {
  const path = href.split('#')[0]?.split('?')[0] ?? '';
  return patterns.some((pattern) => {
    const re = new RegExp(`^${pattern.replace(/:[^/]+/g, '[^/]+')}$`);
    return re.test(path);
  });
}

function check(guide: HelpGuide, patterns: string[]): string[] {
  const problems: string[] = [];
  const ids = new Set(guide.sections.map((s) => s.id));
  const texts: string[] = [];
  for (const section of guide.sections) {
    texts.push(section.title, section.summary);
    for (const item of section.blocks) {
      const inlines: Inline[] = [];
      switch (item.kind) {
        case 'p':
        case 'callout':
          inlines.push(item.text);
          break;
        case 'steps':
        case 'list':
          inlines.push(...item.items);
          break;
        case 'table':
          inlines.push(...item.head, ...item.rows.flat());
          break;
        case 'flow':
          inlines.push(...item.steps.map((s) => `${s.title} ${s.text}`));
          break;
        case 'faq':
          inlines.push(item.q, ...item.a);
          break;
        case 'figure':
          inlines.push(item.caption);
          for (const dir of [DOCS_IMG, APP_IMG]) {
            if (!existsSync(join(dir, item.img))) problems.push(`[${guide.id}#${section.id}] нет снимка ${join(dir, item.img)}`);
          }
          break;
        default:
          inlines.push(item.text);
      }
      for (const text of inlines) {
        texts.push(text);
        for (const token of parseInline(text)) {
          if (token.t !== 'link') continue;
          if (token.href.startsWith('#') && !ids.has(token.href.slice(1))) {
            problems.push(`[${guide.id}#${section.id}] якорь ${token.href} не найден`);
          }
          if (token.href.startsWith('/') && !token.href.startsWith('/api/') && !routeExists(token.href, patterns)) {
            problems.push(`[${guide.id}#${section.id}] маршрута ${token.href} нет в роутере`);
          }
        }
      }
    }
  }
  const all = texts.join('\n');
  for (const [re, why] of FORBIDDEN) {
    if (re.test(all)) problems.push(`[${guide.id}] найдено «${all.match(re)?.[0]}»: ${why}`);
  }
  if (ids.size !== guide.sections.length) problems.push(`[${guide.id}] повторяются id разделов`);
  return problems;
}

// --- main -------------------------------------------------------------------------------

const args = new Set(process.argv.slice(2));
const patterns = routePatterns();
const problems = Object.values(HELP_GUIDES).flatMap((guide) => check(guide, patterns));
if (problems.length) {
  console.error(`Справка: ${problems.length} проблем(ы)\n${problems.join('\n')}`);
  process.exit(1);
}
console.log(`Справка: проверки пройдены (${Object.values(HELP_GUIDES).map((g) => `${g.id}: ${g.sections.length} разд.`).join(', ')})`);

if (!args.has('--check')) {
  for (const guide of Object.values(HELP_GUIDES)) {
    const out = OUTPUTS[guide.id];
    writeFileSync(join(HERE, out.html), render(guide), 'utf8');
    console.log(`  ${out.html}`);
    if (args.has('--pdf')) {
      const chrome = process.env.CHROME ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
      execFileSync(chrome, [
        '--headless',
        '--disable-gpu',
        '--no-pdf-header-footer',
        `--print-to-pdf=${join(HERE, out.pdf)}`,
        `file://${join(HERE, out.html)}`,
      ], { stdio: 'ignore' });
      console.log(`  ${out.pdf}`);
    }
  }
}
