# Редизайн фронтенда CRM «Вузы»: спецификация исполнения

> Ветка: `redesign/shadcn`. Статус: **исполняемая спецификация** — по ней 6 параллельных кодеров переделывают весь фронт без дополнительных вопросов.
> `docs/design/ux.md` остаётся источником правды по **составу экранов, ролям, состояниям и API-контрактам**. Настоящий документ **переопределяет визуальный слой**: библиотеку компонентов, токены, моушн, иллюстрации и добавляет новую фичу — онбординг сотрудников (§7).
> Причина редизайна: заказчик — «недостаточно современно, много текста, мало анимаций/картинок». Ответ: облик shadcn-admin, моушн-язык на framer-motion, набор фирменных SVG-иллюстраций, разгрузка текстовых простыней в карточки/иконки/визуальные акценты.
>
> **Ребрендинг под ДС «Ростелеком» gen2 «Атомаро»** (см. `docs/design/brand-rt.md`): конкретные hex/OKLCH-значения токенов в листингах ниже — **исторические** (первая итерация палитры). Актуальные значения живут в `frontend/src/styles/globals.css` и `frontend/src/shared/config/tokens.ts`: primary = base-info `#1F69FF` (синий РТК, по LMS-референсу rtkb.zion-lms.ru), оранжевый status-01 `#FF4F12` — акцент второго плана, фиолетовый accent-400 `#9233FF` — Talent Pool, сайдбар — графит neutral-950 `#151D2C`, статусные тройки и палитра графиков — из рампов ДС. Архитектура токен-слоя (тройки core/deep/tint, правила контраста, радиусы, моушн) не менялась.

---

## 0. Рамки и зафиксированные решения

### 0.1 Решения (не обсуждаются)

| # | Решение |
|---|---|
| Р1 | UI-кит: **Tailwind CSS v4 + shadcn/ui** (стиль `new-york`, Radix-примитивы). Ant Design 5 и `@ant-design/icons` полностью удаляются из зависимостей по завершении миграции. |
| Р2 | Таблицы: **TanStack Table v8** в обёртке `DataTable`, **API-совместимой с текущим `RegistryTable`** (§3.2) — страницы-реестры мигрируют механической заменой импорта. |
| Р3 | Анимации: пакет **`motion` v12** (это и есть framer-motion 12+; импорт строго `from 'motion/react'`, импорт из `framer-motion` запрещён). Токены/пружины — §2. |
| Р4 | Формы: **react-hook-form + zod** (`@hookform/resolvers/zod`). Контролируемые формы AntD не сохраняются. Маппинг 422 → `setError` — §5.2. |
| Р5 | Иконки: **lucide-react** (в бандле, tree-shakeable). Эмодзи в UI не используются (кроме сообщений TG-бота). |
| Р6 | Тосты: **sonner** (`Toaster` внизу справа), обёртки `toastSuccess/toastError` — §3.11. |
| Р7 | **Остаются и перетемизируются**: Apache ECharts (тема `crm`, §2.6), `@xyflow/react`, `@dnd-kit/*`, `@tanstack/react-query`, `react-router-dom`, `zustand`, `keycloak-js`, `qrcode`, `dayjs`. API-слой (`shared/api`), auth, SSE, guard'ы, `menuConfig` — **не трогаем**. |
| Р8 | Шрифт: **Inter (variable), self-hosted** через `@fontsource-variable/inter` — закрытый контур, никаких CDN и Google Fonts. |
| Р9 | Тёмной темы **нет**. Токены объявляются только в `:root`. `color-scheme: light`. |
| Р10 | Онбординг-тур — **своя реализация** (spotlight + portal + motion), сторонние тур-библиотеки (react-joyride, driver.js и т.п.) запрещены. |
| Р11 | Русский язык всех текстов; словарь терминов из ux.md §4.4 («заявка», «этап», «вуз», «КАМ»). |

### 0.2 Зависимости (добавить / удалить)

```jsonc
// добавить (все из npm, собираются Vite в бандл — без CDN)
"tailwindcss": "^4.1", "@tailwindcss/vite": "^4.1",
"class-variance-authority": "^0.7", "clsx": "^2", "tailwind-merge": "^3",
"tw-animate-css": "^1",                    // keyframes shadcn (npm-пакет, не CDN)
"lucide-react": "^0.5xx",
"motion": "^12",
"sonner": "^2",
"@tanstack/react-table": "^8",
"react-hook-form": "^7", "@hookform/resolvers": "^5", "zod": "^3",
"react-day-picker": "^9",                  // DatePicker/RangePicker (§5.1)
"cmdk": "^1",                              // глобальный поиск Cmd+K
"@fontsource-variable/inter": "^5",
// radix-* добавляются командой shadcn add (см. §3.1)

// удалить по завершении миграции
"antd", "@ant-design/icons", "@fontsource/inter"
```

Vite: подключить `@tailwindcss/vite` плагином; `src/styles/globals.css` — единственная точка входа стилей. Alias `@/` сохраняется.

### 0.3 Definition of Done (каждый экран)

1. Ни одного импорта из `antd`/`@ant-design/icons` в затронутых файлах.
2. Все 4 состояния (загрузка/пусто/пусто-по-фильтрам/ошибка) — на новых компонентах §3, пустые состояния — с иллюстрацией §4.
3. Вход страницы и списков анимирован по §2.3; `prefers-reduced-motion` проверен (DevTools → Rendering → Emulate).
4. Клавиатура: Tab-обход без ловушек, видимое фокус-кольцо, Esc закрывает оверлеи, иконочные кнопки с `aria-label`.
5. Планшет 768px: нет горизонтального скролла страницы (кроме канбана по ux.md §4.3), тач-цели ≥ 40px.
6. `tsc --noEmit` зелёный; функциональность (запросы, guard'ы, SSE, optimistic) идентична ux.md.

---

## 1. Дизайн-токены

### 1.1 `src/styles/globals.css` — канонический листинг

Формат — **OKLCH** (нативно для Tailwind v4). Значения конвертированы из фирменной палитры точно (проверено скриптом); hex-эквиваленты в комментариях — те же, что сейчас в `shared/config/theme.ts`, т.е. **фирменный синий и статусные цвета сохранены бит-в-бит**.

```css
@import 'tailwindcss';
@import 'tw-animate-css';
@import '@fontsource-variable/inter';

:root {
  color-scheme: light;

  /* — поверхность и текст — */
  --background: oklch(0.973 0.006 264.5);        /* #F4F6FA фон layout */
  --card: oklch(1 0 0);                          /* #FFFFFF */
  --popover: oklch(1 0 0);
  --foreground: oklch(0.271 0.025 258.3);        /* #1F2733 основной текст */
  --muted: oklch(0.960 0.009 258.3);             /* #EEF2F8 приглушённые плашки */
  --muted-foreground: oklch(0.522 0.039 253.9);  /* #5A6B80 вторичный текст, AA 5.46:1 */
  --border: oklch(0.916 0.016 257.2);            /* #DDE4EE */
  --input: oklch(0.916 0.016 257.2);
  --ring: oklch(0.459 0.133 256.9);              /* фокус-кольцо = бренд-синий */

  /* — бренд — */
  --primary: oklch(0.459 0.133 256.9);           /* #1E56A0 ФИРМЕННЫЙ СИНИЙ */
  --primary-foreground: oklch(1 0 0);            /* белый на синем, 7.26:1 */
  --primary-hover: oklch(0.521 0.140 257.2);     /* #2E68B8 */
  --primary-active: oklch(0.397 0.113 256.4);    /* #174682 */
  --primary-tint: oklch(0.963 0.012 259.8);      /* #EEF3FB выделение/hover строк */
  --primary-tint-2: oklch(0.924 0.022 254.4);    /* #DCE7F5 активные плашки */
  --sidebar: oklch(0.267 0.069 258.9);           /* #0F2547 тёмно-синий сайдбар */
  --sidebar-foreground: oklch(1 0 0);
  --sidebar-muted: oklch(0.787 0.043 258.4);     /* #A9BBD6 вторичное в сайдбаре, 7.82:1 */
  --sidebar-active: oklch(0.459 0.133 256.9);    /* пункт меню активный = primary */

  --secondary: oklch(0.960 0.009 258.3);         /* secondary-кнопки: серо-голубая плашка */
  --secondary-foreground: oklch(0.271 0.025 258.3);
  --accent: oklch(0.963 0.012 259.8);            /* hover меню/строк = primary-tint */
  --accent-foreground: oklch(0.397 0.113 256.4);
  --destructive: oklch(0.550 0.181 29.1);        /* #C5372B */
  --destructive-foreground: oklch(1 0 0);

  /* — семантические статусы: тройки core / deep / tint (§1.2) — */
  --status-draft: oklch(0.522 0.039 253.9);        /* #5A6B80 */
  --status-draft-deep: oklch(0.441 0.042 256.4);   /* #44546A */
  --status-draft-tint: oklch(0.960 0.009 258.3);   /* #EEF2F8 */
  --status-progress: oklch(0.459 0.133 256.9);     /* #1E56A0 */
  --status-progress-deep: oklch(0.397 0.113 256.4);/* #174682 */
  --status-progress-tint: oklch(0.963 0.012 259.8);/* #EEF3FB */
  --status-success: oklch(0.621 0.141 152.7);      /* #2E9E5B */
  --status-success-deep: oklch(0.514 0.117 152.7); /* #217A45 */
  --status-success-tint: oklch(0.957 0.019 157.9); /* #E7F5EC */
  --status-warning: oklch(0.678 0.155 62.6);       /* #D97E00 */
  --status-warning-deep: oklch(0.531 0.119 65.1);  /* #9A5B00 */
  --status-warning-tint: oklch(0.960 0.025 75.3);  /* #FCF0E0 */
  --status-danger: oklch(0.550 0.181 29.1);        /* #C5372B */
  --status-danger-deep: oklch(0.499 0.172 29.4);   /* #B02A1F */
  --status-danger-tint: oklch(0.950 0.019 25.6);   /* #FBEAE8 */
  --status-talent: oklch(0.531 0.169 298.1);       /* #7A4FBF */
  --status-talent-deep: oklch(0.473 0.159 296.9);  /* #6741A8 */
  --status-talent-tint: oklch(0.949 0.021 304.0);  /* #F1EBFA */

  /* — категориальная палитра графиков (§2.6, валидирована на CVD) — */
  --chart-1: oklch(0.459 0.133 256.9);  /* #1E56A0 бренд-синий */
  --chart-2: oklch(0.615 0.147 58.2);   /* #C46A00 оранжевый */
  --chart-3: oklch(0.590 0.104 178.8);  /* #12917E морской */
  --chart-4: oklch(0.547 0.180 299.6);  /* #8250C8 фиолетовый */
  --chart-5: oklch(0.558 0.172 6.7);    /* #C13B63 малиновый */
  --chart-6: oklch(0.539 0.114 75.2);   /* #946300 охра */

  /* — геометрия — */
  --radius: 0.625rem;          /* 10px базовый (карточки, инпуты через calc) */
  --radius-sm: calc(var(--radius) - 4px);   /* 6px  чипы, теги */
  --radius-md: calc(var(--radius) - 2px);   /* 8px  кнопки, инпуты */
  --radius-lg: var(--radius);               /* 10px карточки */
  --radius-xl: calc(var(--radius) + 6px);   /* 16px модалки, иллюстрационные панели */

  /* — тени (только два уровня + drag) — */
  --shadow-card: 0 1px 2px oklch(0.30 0.05 258 / 0.06), 0 4px 12px oklch(0.30 0.05 258 / 0.08);
  --shadow-overlay: 0 6px 20px oklch(0.30 0.05 258 / 0.12);
  --shadow-drag: 0 12px 32px oklch(0.30 0.05 258 / 0.22);
}

@theme inline {
  --font-sans: 'Inter Variable', 'Segoe UI', system-ui, sans-serif;
  --color-background: var(--background);
  --color-foreground: var(--foreground);
  --color-card: var(--card);
  --color-popover: var(--popover);
  --color-muted: var(--muted);
  --color-muted-foreground: var(--muted-foreground);
  --color-border: var(--border);
  --color-input: var(--input);
  --color-ring: var(--ring);
  --color-primary: var(--primary);
  --color-primary-foreground: var(--primary-foreground);
  --color-primary-hover: var(--primary-hover);
  --color-primary-active: var(--primary-active);
  --color-primary-tint: var(--primary-tint);
  --color-primary-tint-2: var(--primary-tint-2);
  --color-sidebar: var(--sidebar);
  --color-sidebar-foreground: var(--sidebar-foreground);
  --color-sidebar-muted: var(--sidebar-muted);
  --color-sidebar-active: var(--sidebar-active);
  --color-secondary: var(--secondary);
  --color-secondary-foreground: var(--secondary-foreground);
  --color-accent: var(--accent);
  --color-accent-foreground: var(--accent-foreground);
  --color-destructive: var(--destructive);
  --color-destructive-foreground: var(--destructive-foreground);
  /* статусные и chart-токены — по тому же шаблону color-status-*, color-chart-* */
  --radius-sm: var(--radius-sm); --radius-md: var(--radius-md);
  --radius-lg: var(--radius-lg); --radius-xl: var(--radius-xl);
  --shadow-card: var(--shadow-card); --shadow-overlay: var(--shadow-overlay);
  --shadow-drag: var(--shadow-drag);
}

@layer base {
  body { @apply bg-background text-foreground font-sans antialiased; font-size: 14px; }
  :focus-visible { @apply outline-none ring-2 ring-ring ring-offset-2 ring-offset-background; }
  .tabular { font-variant-numeric: tabular-nums; }
}
```

Правила:
- **Никаких hex в компонентах** — только токены (`bg-primary`, `text-status-warning-deep`, `bg-status-warning-tint`). `STATUS_COLORS` из `shared/config/theme.ts` переписывается на экспорт этих же значений (hex остаются для ECharts/xyflow, где нужны строки) — единый источник в `shared/config/tokens.ts`.
- `outline-none` без замены на ring запрещён (web-interface-guidelines).

### 1.2 Статусные цвета: тройки и совместимость

Каждый семантический статус — тройка: **core** (маркеры, полосы, графики), **deep** (текст — везде, где статусный цвет является текстом), **tint** (фон бейджей/плашек). Core-значения **совпадают с текущими** `STATUS_COLORS` — данные (цвета этапов workflow в конфиге схемы) не мигрируются.

Проверенные пары контраста (WCAG AA, посчитано):

| Пара | Ratio | Норма |
|---|---|---|
| `foreground` #1F2733 на white / на `background` | 15.04 / 13.90 | ≥4.5 ✔ |
| `muted-foreground` #5A6B80 на white / на `background` | 5.46 / 5.04 | ≥4.5 ✔ |
| white на `primary` #1E56A0 (кнопки) | 7.26 | ≥4.5 ✔ |
| `primary` как ссылка на white; на `primary-tint` | 7.26 / 6.51 | ≥4.5 ✔ |
| white на `sidebar` #0F2547; `sidebar-muted` на sidebar | 15.26 / 7.82 | ≥4.5 ✔ |
| `success-deep` #217A45 на white / на `success-tint` | 5.34 / 4.75 | ≥4.5 ✔ |
| `warning-deep` #9A5B00 на white / на `warning-tint` | 5.43 / 4.83 | ≥4.5 ✔ |
| `danger-deep` #B02A1F на white / на `danger-tint` | 6.57 / 5.64 | ≥4.5 ✔ |
| `talent-deep` #6741A8 на white / на `talent-tint` | 7.25 / 6.21 | ≥4.5 ✔ |
| `draft-deep` #44546A на `draft-tint` | 6.87 | ≥4.5 ✔ |

⚠ Ловушка: **core-цвета `#2E9E5B` (3.41:1) и `#D97E00` (3.03:1) как текст на белом НЕ проходят AA** — текстом всегда идёт `-deep`, core только для заливок/маркеров/иконок ≥ 3:1-графики. Это инвариант `StatusBadge` (§3.6), руками статусные цвета на текст не вешать.

Пресет 8 цветов этапа в конструкторе workflow (панель свойств; сохранённые в конфигах этапов значения валидны): `#1E56A0`, `#C46A00`, `#12917E`, `#8250C8`, `#C13B63`, `#946300`, `#2E9E5B`, `#5A6B80`. Тинт-фон колонки/бейджа этапа вычисляется в рантайме: `color-mix(in oklab, <stageColor> 10%, white)`, текст — `color-mix(in oklab, <stageColor> 78%, black)` (это даёт ≥4.5 для всех восьми; цвет этапа никогда не идёт чистым текстом).

### 1.3 Типографика

Inter Variable, self-hosted (`@fontsource-variable/inter`, woff2 в бандле). Шкала:

| Токен (tailwind) | Размер/интерлиньяж | Вес | Употребление |
|---|---|---|---|
| `text-[28px] leading-9` + `.tabular` | 28/36 | 650 | Цифры KPI (StatCard) |
| `text-xl` (20/28) | 20/28 | 600 | H1 экрана (PageHeader) |
| `text-base` (16/24) | 16/24 | 600 | H2 секции, заголовки карточек |
| `text-sm` (14/22, база body) | 14/22 | 400/500 | Тело, ячейки таблиц |
| `text-xs` (12/18) | 12/18 | 400/500 | Вторичный текст, метки, бейджи |
| `text-[11px] uppercase tracking-wide` | 11/16 | 600 | Заголовки колонок таблиц, оверлайны |

Правила: числа в колонках и KPI — `tabular-nums`; троеточие `…` (не `...`); заголовки — `text-balance`; текст в flex-ячейках — `min-w-0` + `truncate`; неразрывный пробел в «10 МБ», «Cmd K».

### 1.4 Spacing и сетка

8px-сетка. Контент: `max-w-[1440px] mx-auto px-6` (планшет `px-4`), вертикальный ритм секций 24px, внутри карточек 20px, между связанными элементами 8/12px. Сайдбар 248px / свёрнутый 64px, хедер 56px. Карточки: `rounded-lg border bg-card shadow-card` — теней два уровня, у **вложенных** блоков теней и карточек нет (никаких card-in-card, разделение — `border-t` или фон `muted`).

---

## 2. Моушн-язык

### 2.1 Токены — `src/shared/lib/motion.ts` (единственный источник значений)

```ts
export const motionTokens = {
  duration: { instant: 0.08, fast: 0.18, normal: 0.35, slow: 0.6 },
  easing: {
    smooth: [0.22, 1, 0.36, 1] as const,   // вход элементов
    sharp:  [0.4, 0, 0.2, 1] as const,     // выход/сворачивание
  },
  distance: { xs: 4, sm: 8, md: 16, lg: 24 },
  scale: { subtle: 0.98, press: 0.97, pop: 1.02 },
} as const;

export const springs = {
  snappy:  { type: 'spring', stiffness: 300, damping: 30 },  // дефолт UI: чипы, кнопки, сегменты
  gentle:  { type: 'spring', stiffness: 120, damping: 14 },  // карточки, панели, модалки
  bouncy:  { type: 'spring', stiffness: 400, damping: 10 },  // онбординг, иллюстрационные моменты
  instant: { type: 'spring', stiffness: 600, damping: 35 },  // тултипы, поповеры, дропдауны
  release: { type: 'spring', stiffness: 200, damping: 20, restDelta: 0.001 }, // отпускание drag
} as const;
```

Инлайновые `duration`/`stiffness` в компонентах **запрещены** — только импорт токенов. CSS-переходы (hover) — те же значения: `transition-[color,background-color,border-color,box-shadow,transform] duration-150` (≈`fast`); `transition: all` запрещён.

### 2.2 Жёсткие правила

1. Анимируются только `transform` и `opacity` (+ `box-shadow` на hover). `width/height/top/left/margin` — никогда (аккордеоны — `scaleY` + `transform-origin: top`, либо Radix `Collapsible` с его keyframes).
2. Условный рендер с exit-анимацией — всегда `AnimatePresence` + `key` + `exit` (все три, иначе exit молча не сработает).
3. Смена страниц — `AnimatePresence mode="wait"`; стеки тостов/списки — `mode="popLayout"`.
4. Stagger списков — **0.05–0.08s** на элемент, максимум 12 анимируемых элементов (дальше без задержки); `layout`-анимации только на малых изолированных участках (< ~5 детей).
5. Вьюпорт-анимации (`whileInView`) — `viewport={{ once: true }}`.
6. Каждая анимация обязана: направлять внимание / сообщать состояние / сохранять пространственную непрерывность. Не выполняет ни одного — удаляется.

### 2.3 Каталог анимаций (что и как)

| Паттерн | Спека |
|---|---|
| **Вход страницы** | Обёртка `PageTransition` вокруг `<Outlet/>` (key = pathname): initial `{opacity: 0, y: 8}` → `{opacity: 1, y: 0}`, `duration.normal`, `easing.smooth`, `AnimatePresence mode="wait"`. Exit `{opacity: 0, y: -8}`, `duration.fast`. |
| **Stagger карточек/списков** | Контейнер-variants `staggerChildren: 0.06, delayChildren: 0.05`; элемент `{opacity 0, y: distance.md}` → `springs.gentle`. Применять: KPI-ряд, виджеты дашборда, колонки канбана (по колонкам, не по карточкам), карточки каналов нотификаций, строки чек-листа онбординга. Строки DataTable НЕ стаггерятся (ежедневный инструмент — скорость важнее). |
| **Канбан-drag** | Захват: `scale: 1.02`, `shadow-drag`, `cursor-grabbing`, исходник `opacity 0.35` (как сейчас). Отпускание в разрешённую колонку: перелёт `springs.release`, затем короткий пульс фона карточки `primary-tint` → прозрачный (`duration.normal`). Запрещённый дроп: возврат `springs.release` + shake колонки-цели запрещён — только toast. Подсветка разрешённых колонок: `ring-2 ring-primary/60` появление `duration.fast`. |
| **Hover карточек** | Интерактивные карточки (BoardCard, StatCard-кликабельный, карточки связей): `hover:-translate-y-0.5 hover:shadow-overlay`, CSS 150ms. Кнопки: `whileTap={{ scale: scale.press }}` + `springs.snappy` (`MotionButton`-обёртка не нужна — достаточно CSS `active:scale-[0.97]`). |
| **Count-up KPI** | Существующий `useCountUp` остаётся; длительность 0.8s, easing `smooth`; форматирование `tabular-nums`. При reduced-motion — мгновенно финальное значение. |
| **Вход графиков** | Карточка виджета — в общем stagger'e; сам ECharts: `animationDuration: 600`, `animationEasing: 'cubicOut'`, `animationDelay: (i) => i * 40` (в теме `crm`, §2.6). Повторные перерисовки (смена фильтра) — `animationDurationUpdate: 240`. |
| **Skeleton-перелив** | Компонент `Skeleton` shadcn + shimmer: `@keyframes shimmer {from {background-position: 200% 0} to {background-position: -200% 0}}`, градиент `linear-gradient(90deg, var(--muted) 25%, var(--primary-tint) 50%, var(--muted) 75%)`, `background-size: 200% 100%`, 1.6s linear infinite. Скелетон повторяет каркас экрана (см. макеты состояний в ux.md §4.1). |
| **Оверлеи** | Dialog/Sheet/Popover — присеты shadcn (tw-animate-css): fade + `scale 0.97→1` / slide 8px, 180ms. Sheet карточки заявки — slide-in справа `duration.normal`. |
| **Тосты** | sonner дефолт (slide+fade), `richColors` выключен — цвета из токенов. |
| **Онбординг-spotlight** | Перемещение подсветки между шагами — `layout`-анимация рамки `springs.gentle`; поповер шага `{opacity, y: 8, scale 0.98}` `springs.gentle`. |
| **Микро-фидбек** | Успешное сохранение инлайн-поля — вспышка фона `success-tint` 400ms; ошибка поля — без shake, только цвет+текст. Колокольчик при новом событии — one-shot `rotate: [0,-12,10,-6,0]` 0.5s. |

### 2.4 `prefers-reduced-motion` (обязательно)

- В корне приложения: `<MotionConfig reducedMotion="user">` — motion сам отключает transform-анимации, оставляя opacity.
- Хук `useSafeMotion(y)` (из скилла motion-foundations) — для ручных variants: при reduce `y: 0`, только fade ≤ 0.2s.
- CSS: `@media (prefers-reduced-motion: reduce) { .shimmer { animation: none } }`; count-up → мгновенно; ECharts: `animation: false` в теме при reduce (проверять `matchMedia` при построении option).
- Проверка в DoD каждого экрана.

### 2.5 Чего НЕ делаем

Параллакс, скролл-приколы, автоплей-анимации дольше 5с, декоративные лупы, анимация layout-свойств, stagger > 0.1s, анимации, маскирующие медленный запрос (вместо этого — skeleton/`isFetching`-индикатор).

### 2.6 Тема ECharts `crm` (перекраска графиков — по скиллу dataviz)

`src/shared/config/echartsTheme.ts`, регистрируется один раз (`echarts.registerTheme('crm', …)`), все `ReactECharts` получают `theme="crm"`.

- **Категориальная палитра (фиксированный порядок, никогда не циклится):** `['#1E56A0', '#C46A00', '#12917E', '#8250C8', '#C13B63', '#946300']`. Палитра **прогнана через валидатор dataviz: PASS** по всем проверкам (полоса светлоты, хрома-флор, CVD-разделение соседних пар ΔE≥8, normal-vision floor, контраст ≥3:1 к поверхности). 7-я серия не генерируется — складывается в «Прочее». Цвет закреплён за сущностью, не за рангом: смена фильтра не перекрашивает выжившие серии.
- **Статусные цвета зарезервированы** (§1.2) и в категориальные серии не подмешиваются; воронка этапов красится цветами этапов из конфига workflow (как сейчас), воронка talent pool — рамп фиолетового: `['#7A4FBF','#9673CC','#B29ADB','#CFC2EA']` (светлеет к концу, identity по подписям сегментов).
- **Один y-axis всегда**; две метрики разного масштаба — два графика/small multiples.
- Оси/сетка рецессивные: axisLine/tickLine `#DDE4EE`, splitLine `#EEF2F8`, подписи `#5A6B80` 12px Inter; текст никогда не красится цветом серии.
- Марки: line width 2 + symbol 8 (показывать на hover), bar `borderRadius: [4,4,0,0]`, зазор между stacked-сегментами и сегментами воронки — 2px белая обводка (`itemStyle.borderWidth: 2, borderColor: '#fff'`), area-градиент `rgba(30,86,160,0.18) → 0`.
- Tooltip: фон white, border `#DDE4EE`, radius 8, тень `shadow-overlay`, текст `#1F2733`; crosshair на line.
- Легенда всегда при ≥2 сериях (одна серия — без легенды, её называет заголовок), иконка `circle` 8px.
- Анимации — §2.3.

---

## 3. Библиотека компонентов `src/shared/ui`

### 3.1 База shadcn/ui (генерируется CLI, затем правится под токены)

```
npx shadcn@latest add button badge card dialog alert-dialog sheet dropdown-menu \
  select popover command tooltip input textarea checkbox radio-group switch \
  tabs table skeleton avatar breadcrumb separator scroll-area progress label \
  alert collapsible calendar form sonner pagination
```

Компоненты лежат в `src/shared/ui/` (настроить `components.json`: `"ui": "@/shared/ui"`). Правки после генерации: цвета — только токены §1.1; `Button` — варианты `default | secondary | outline | ghost | destructive | link`, размеры `sm(32) | default(36) | lg(40) | icon(36)`; фокус-кольца не удалять.

### 3.2 `DataTable<T>` — замена RegistryTable (TanStack Table)

Файл `src/shared/ui/data-table/`. **Контракт пропсов = текущий `RegistryTableProps`** (см. `frontend/src/components/RegistryTable/types.ts`) — сохранить имена и семантику 1-в-1, чтобы миграция страниц была заменой импорта:

```ts
export interface DataTableColumn<T> {
  key: string;                                   // сортировка, видимость, пресеты
  title: string;
  dataIndex?: keyof T & string;                  // по умолчанию = key
  render?: (value: unknown, record: T) => ReactNode;
  sorter?: boolean;                              // серверная сортировка по key
  width?: number | string;
  align?: 'left' | 'center' | 'right';
  ellipsis?: boolean;                            // truncate + title
  responsive?: Array<'md' | 'lg' | 'xl'>;        // скрыть уже брейкпоинта
  alwaysVisible?: boolean;                       // нельзя скрыть в «Колонках»
}

export interface DataTableProps<T> {
  screen: string;                                // ключ пресетов (ux.md §4.2)
  columns: DataTableColumn<T>[];
  rowKey: keyof T & string;
  fetcher: (p: RegistryFetchParams) => Promise<ListEnvelope<T>>;  // НЕ меняется
  searchPlaceholder?: string;
  renderFilters?: (ctx: { filters: RegistryFilters; setFilter: (k, v) => void }) => ReactNode;
  defaultFilters?: RegistryFilters;
  defaultSort?: RegistrySort | null;
  toolbar?: ReactNode;
  exportEntityType?: string;                     // кнопка «Экспорт» через /export/jobs
  emptyTitle?: string;
  emptyDescription?: ReactNode;
  emptyAction?: ReactNode;
  emptyIllustration?: IllustrationName;          // НОВОЕ: сюжет пустого состояния (§4)
  onRowClick?: (record: T) => void;
}
```

Внутренности: `useReactTable` c `manualPagination/manualSorting: true`, состояние — тот же `TableState` и react-query запрос, что сейчас (`keepPreviousData`, debounce поиска 400ms, AbortSignal). UI: панель (поиск с иконкой `Search`, `renderFilters`, справа — Пресеты / Колонки (`DropdownMenu` c чекбоксами) / Экспорт / toolbar), таблица `Table` shadcn (шапка `bg-muted text-[11px] uppercase tracking-wide text-muted-foreground`, hover строки `bg-primary-tint`, сортируемые заголовки — кнопка с `ArrowUpDown/ArrowUp/ArrowDown`, `aria-sort`), футер-пагинация (строки «1–20 из 134», выбор 20/50/100, prev/next). Обновление при `isFetching` — тонкий индикатор-полоска `bg-primary` 2px над шапкой, без миганий. Пресеты: перенести `PresetsControl` на shadcn `Select`+`Dialog` без изменения API-вызовов (`/ui/presets`). Пустые состояния — `EmptyState`/`FilteredEmptyState` (§3.5). Клавиатура: строки при `onRowClick` — `tabIndex=0`, Enter открывает. Файл `components/RegistryTable/` реэкспортирует `DataTable` под старым именем до конца миграции.

### 3.3 `PageHeader`

```ts
interface PageHeaderProps {
  title: string;                 // H1 20/28 semibold, text-balance
  subtitle?: ReactNode;          // 1 строка text-muted-foreground; НЕ абзацы
  extra?: ReactNode;             // действия справа (primary-кнопка экрана)
  backTo?: string;               // стрелка назад (детальные страницы)
  meta?: ReactNode;              // строка бейджей/фактов под заголовком (StatusBadge, даты)
}
```

Вход: title/extra — в общем PageTransition, без собственной анимации.

### 3.4 `StatCard` (замена Statistic/KPI-карточек)

```ts
interface StatCardProps {
  title: string;
  icon: LucideIcon;
  tone: 'primary' | 'success' | 'warning' | 'danger' | 'talent' | 'draft';
  value: number;
  suffix?: string;               // '%'
  delta?: number;                // стрелка к прошлому периоду
  invertedDelta?: boolean;       // рост = плохо (зависшие)
  hint?: string;                 // тултип
  onClick?: () => void;          // drill-down; карточка становится кнопкой
  loading?: boolean;             // скелетон той же геометрии
}
```

Вид: иконка в тонированном квадрате 40px (`bg-{tone}-tint text-{tone}-deep rounded-md`), число 28/36 count-up `tabular-nums`, дельта — `TrendingUp/Down` + `text-status-success-deep|danger-deep` 12px. Кликабельная — `hover:-translate-y-0.5 hover:shadow-overlay`, `aria-label` с расшифровкой.

### 3.5 `EmptyState` (с иллюстрациями)

```ts
interface EmptyStateProps {
  illustration: IllustrationName;      // §4; обязателен — «больше картинок»
  title: string;                       // ≤ 6 слов
  description?: string;                // ≤ 1 предложение
  actions?: ReactNode;                 // 1 primary + до 1 secondary CTA
  compact?: boolean;                   // внутри виджетов/ячеек: SVG 96px, без description
}
```

`FilteredEmptyState { onReset }` — сюжет `search-empty` + кнопка «Сбросить фильтры». `ErrorState { error, onRetry, title? }` — сюжет `error`, текст без стектрейса, requestId мелким `text-xs text-muted-foreground`, кнопка «Повторить». Вход — `springs.bouncy` подскок иллюстрации (только opacity при reduce).

### 3.6 `StatusBadge` (замена Tag)

```ts
type SemanticStatus = 'draft' | 'progress' | 'success' | 'warning' | 'danger' | 'talent';
interface StatusBadgeProps {
  status?: SemanticStatus;         // семантические тройки из §1.2
  color?: string;                  // ИЛИ произвольный цвет этапа workflow (hex из конфига)
  icon?: LucideIcon;               // статус никогда не только цветом: иконка или точка
  children: ReactNode;
  size?: 'sm' | 'md';
}
```

Рендер: `rounded-sm px-2 py-0.5 text-xs font-medium`, фон tint, текст deep (для `color` — `color-mix` формула §1.2), слева точка 6px core-цвета или иконка 12px. Специализации поверх: `StageTag {name,color}` (совместим с текущим), `ContractStatusTag {status}` (словарь из текущего `StatusTag.tsx`), `FunnelStatusBadge`, бейдж «зависла»: `status="warning"`, icon `Flame`, текст «N дн. без движения».

### 3.7 `Timeline`

```ts
interface TimelineItem {
  id: string;
  icon: LucideIcon;                        // тип события: ArrowRightLeft, Paperclip, MessageSquare, Plug, Bell…
  tone: SemanticStatus;                    // возврат = 'danger' (красный «↩»)
  title: ReactNode;                        // «Иванов перевёл: Переговоры → Согласование»
  time: string;                            // относительное до 24ч, далее дата (ux.md §4.4)
  content?: ReactNode;                     // комментарий причины, ссылка «показать JSON»
}
interface TimelineProps { items: TimelineItem[]; filter?: ReactNode /* чипы над лентой */ }
```

Вид: вертикальная линия `border-l border-border`, узлы — иконка 24px в круге `bg-{tone}-tint text-{tone}-deep` (визуально считывается без чтения текста — ответ на «много текста»). Возврат: узел danger + метка «↩ Возврат». Вход новых элементов (SSE) — `AnimatePresence mode="popLayout"`, fade+y `springs.gentle`.

### 3.8 `ConfirmDialog` (красные сценарии, посимвольный ввод)

```ts
interface ConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  tone?: 'danger' | 'default';
  title: string;                          // «Удаление этапа "Согласование договора"»
  facts?: Array<{ icon?: LucideIcon; label: ReactNode }>;  // блок фактов плашками, не абзацем
  body?: ReactNode;                       // напр. radio выбора этапа миграции
  confirmWord?: string;                   // посимвольное подтверждение: кнопка активна
                                          // только при точном совпадении ввода (значение
                                          // уходит наружу как confirm_name)
  confirmLabel: string;                   // «Удалить и отправить на согласование»
  loading?: boolean;
  onConfirm: (typed?: string) => void;
}
```

Radix AlertDialog: focus-trap, Esc, scroll-lock, `role="alertdialog"` из коробки. Поле ввода: `autocomplete="off" spellCheck={false}`, paste разрешён, подпись «Введите название этапа для подтверждения», прогресс совпадения — набранные верные символы подсвечиваются `text-status-danger-deep`. Кнопка `variant="destructive"` disabled до совпадения.

### 3.9 `FileDropzone` (замена Upload.Dragger)

```ts
interface FileDropzoneProps {
  accept: string[];              // ['.xlsx','.xls','.csv','.json'] / ['.pdf','.docx',…]
  maxSizeMb: number;
  multiple?: boolean;
  onFiles: (files: File[]) => void;
  illustration?: IllustrationName;   // 'import-drop' в мастере
  hint?: string;                     // «XLSX, XLS, CSV или JSON · до 20 МБ»
  uploading?: { name: string; percent: number } | null;  // Progress
}
```

Свой DnD (dragover-состояние `border-primary bg-primary-tint`, dashed border), скрытый `<input type="file">` с `label`, ошибки формата/размера — inline `text-status-danger-deep`, не toast.

### 3.10 Прочие кастомные

| Компонент | Контракт | Примечание |
|---|---|---|
| `Stepper` | `{ steps: {id, title, hint?}[]; current: number }` | Мастер импорта: кружки-числа → галки `Check`, соединительная линия заливается `bg-primary` (transform scaleX, `duration.normal`). |
| `SegmentedControl` | `{ options: {value, label, icon?, count?}[]; value; onChange; size? }` | B2B/B2C, «Все/Месяц/Квартал/Год». Radix ToggleGroup + скользящая подложка `layoutId="segment-thumb"` (`springs.snappy`). |
| `CountUp` | `{ value: number; format?: (n) => string }` | Обёртка над текущим `useCountUp` + reduce-гейт. |
| `Illustration` | `{ name: IllustrationName; className?; height? }` | Реестр инлайн-SVG §4, `aria-hidden="true"`. |
| `SearchCommand` | без пропсов; глобальный | cmdk-палитра (Cmd+K): группы Вузы/Заявки/Студенты через `/api/v1/search?q=`. |
| `InlineEdit` | `{ value; onSave(v); type: 'text'|'number'|'date'; formatter? }` | Атрибуты карточки заявки: клик → input → blur/Enter сохраняет (optimistic), вспышка `success-tint`. |
| `MentionsTextarea` | `{ value; onChange; users: {id,name}[] }` | Комментарии: `@` открывает cmdk-поповер, подставляет имя. |
| `UserAvatar` | текущий контракт | Перенести на shadcn `Avatar` (инициалы, детерминированный фон из 6 chart-цветов по hash id). |

### 3.11 Toaster

sonner: `<Toaster position="bottom-right" />`. Обёртки в `shared/lib/toast.ts`: `toastSuccess(msg)`, `toastError(error)` — достаёт `detail`/requestId из ApiError (существующий `errorMessage()`), кнопка «Повторить» через `action`. Иконки lucide, цвета — статусные токены. Все `message.*`/`notification.*` AntD заменяются этими обёртками.

---

## 4. Иллюстрации: инлайн-SVG, лёгкий дуотон

### 4.1 Стиль (единый, отступать нельзя)

- **Дуотон бренда**: контурные линии `#0F2547` (stroke 1.5, `round` joins/caps), заливки-плоскости `#EEF3FB` и `#DCE7F5`, один акцент `#1E56A0` (≤ 20% площади). Для talent pool-сюжетов акцент `#7A4FBF`, для danger-сюжетов `#C5372B` — по одному акценту на сюжет.
- Геометрия: viewBox `0 0 240 160`, отрисовка на 8px-сетке, скругления 4–8px, простые формы (изометрии и людей с лицами не рисуем — предметный минимализм: документы, доски, папки, графики, лупа, рука-курсор). 12–25 путей на сюжет, без градиентов и фильтров (кроме одной подложки-«тени» `#EEF2F8` эллипсом).
- Техника: каждый сюжет — React-компонент в `src/shared/ui/illustrations/` (инлайн `<svg>`, **никаких внешних файлов/ассетов**), цвета — токены через `var(--…)` c hex-фолбэком, `aria-hidden="true"`, `width/height` заданы (нет CLS). Реестр `Illustration` (§3.10) по `IllustrationName`.
- Размеры употребления: empty-state 160–200px высоты, compact 96px, login/onboarding 200–240px.

### 4.2 Сюжеты (12 штук — отрисовать все)

| `IllustrationName` | Композиция | Где |
|---|---|---|
| `board-empty` | Канбан-доска: 3 колонки, пунктирная карточка с плюсом, курсор-рука тянет карточку | пустой канбан |
| `registry-empty` | Стопка карточек-папок с ярлыками, верхняя приоткрыта, акцентный ярлык | пустые реестры |
| `students-empty` | Бейдж-пропуск студента с маскированными строками (полоски вместо текста) + фиолетовая академическая шапочка | talent pool пуст (акцент `#7A4FBF`) |
| `search-empty` | Лупа над листом с полосками, знак вопроса в линзе | «ничего не найдено по фильтрам», пустой поиск Cmd+K |
| `import-drop` | Лист-таблица «влетает» в открытую папку, пунктирная траектория, стрелка вниз | дропзона мастера импорта, пустые реестры (второй CTA) |
| `import-done` | Лист с галкой в кружке-акценте, рядом маленькая стопка обработанных строк | финал импорта (Result success) |
| `login-hero` | Здание вуза с колоннами + щит с галкой (SSO), соединённые пунктиром узлы | левая панель логина |
| `error-broken` | Оборванный провод-ломаная между двумя узлами, искры-чёрточки (акцент danger) | ErrorState, offline-баннер, 500 |
| `error-403` | Дверь с замком и тег «наблюдатель» | /403 |
| `error-404` | Карта-схема с пунктирным маршрутом в никуда, флажок | /404, «заявка не найдена» |
| `onboarding-welcome` | Дорожка из 3 флажков-шагов к кубку, конфетти-точки (акцент по роли: синий kam/admin, фиолетовый — не нужен) | welcome-экран онбординга |
| `notifications-empty` | Колокольчик со «спящей» z-z-z пунктирной волной | пустой поповер колокольчика; карточка «TG не привязан» |

Кодер отрисовывает по описанию; критерий приёмки — сюжет опознаётся без подписи, стиль неотличим от соседних (одна толщина линий, одна палитра).

---

## 5. Маппинг AntD → новое (обязателен к соблюдению на каждом экране)

### 5.1 Таблица соответствий

| AntD | Замена | Примечания |
|---|---|---|
| `Table` | `DataTable` §3.2 | Реестры, admin, talent pool. Мелкие статические таблицы (мини-таблица в конструкторе, матрица подписок) — `Table` shadcn напрямую. |
| `Form`/`Form.Item` | `Form` shadcn + react-hook-form + zod | §5.2 |
| `Modal` | `Dialog`; подтверждения — `AlertDialog`/`ConfirmDialog` §3.8 | `Modal.confirm` императивный запрещён — декларативный state |
| `Drawer` | `Sheet side="right"` | Карточка заявки поверх доски — `sm:max-w-[720px]`; формы создания — `sm:max-w-[480px]` |
| `Tabs` | `Tabs` shadcn | Подчёркнутый вариант (list `border-b`, активный таб `border-b-2 border-primary text-primary`) |
| `Tag` | `StatusBadge`/`Badge` §3.6 | Никогда не цвет-только: точка/иконка + текст |
| `Select` | `Select` shadcn; с поиском/мульти — `Popover + Command` (паттерн Combobox) | мультиселекты фильтров: чипы выбранного внутри триггера, счётчик «+N» |
| `DatePicker`/`RangePicker` | `Popover + Calendar` (react-day-picker, locale ru) | пресеты периодов («Месяц/Квартал/Год») — кнопки в футере поповера |
| `Upload.Dragger` | `FileDropzone` §3.9 | |
| `Steps` | `Stepper` §3.10 | |
| `Statistic` | `StatCard` §3.4 | |
| `Descriptions` | Паттерн `DL`: `<dl class="grid grid-cols-[140px_1fr] gap-y-2 text-sm">`, `dt` — `text-muted-foreground` | Реквизиты вуза/договора |
| `Timeline` | `Timeline` §3.7 | |
| `Segmented` | `SegmentedControl` §3.10 | |
| `Breadcrumb` | `Breadcrumb` shadcn | генерация из route как сейчас |
| `Dropdown` | `DropdownMenu` | «Перевести на этап ▾» — с подсекцией «↩ Возврат на…» (`DropdownMenuLabel` + danger-пункты) |
| `Popover`/`Tooltip` | `Popover` / `Tooltip` shadcn | Tooltip delay 300ms; на disabled-элементы — оборачивать span |
| `message`/`notification` | sonner §3.11 | |
| `Result` | `ErrorState`/полноэкранные страницы с иллюстрацией §4 | |
| `Empty` | `EmptyState` §3.5 | |
| `Skeleton` | `Skeleton` + shimmer §2.3 | |
| `Badge` (счётчик) | `Badge` shadcn / точка-индикатор | счётчик непрочитанных на колокольчике |
| `Avatar` | `UserAvatar` на shadcn `Avatar` | |
| `Card` | `div.rounded-lg.border.bg-card.shadow-card` (+ `CardHeader/Content` shadcn) | без вложенных карточек |
| `Collapse` | `Collapsible` | тех-справки, «Управлять пресетами» |
| `Switch`/`Checkbox`/`Radio` | одноимённые shadcn | label и контрол — один hit-target (`<label>` обёртка) |
| `InputNumber` | `Input type="number"` + `inputmode="numeric"` + кнопки ±  | пороги нотификаций, board_order |
| `Mentions` | `MentionsTextarea` §3.10 | |
| `Progress` | `Progress` shadcn | загрузка файла, прогресс импорта, чек-лист онбординга |
| `Grid/Row/Col`, `Flex`, `Space`, `Layout` | Tailwind: `grid grid-cols-12 gap-4`, `flex gap-*` | `Grid.useBreakpoint` → хук `useBreakpoint` на `matchMedia` в `shared/lib` |
| `Typography` | классы шкалы §1.3 | |
| `QRCode`-модалка TG | остаётся пакет `qrcode` → canvas | не AntD-компонент, менять не надо |

### 5.2 Формы: единый шаблон

- Схема zod рядом с формой (`const schema = z.object({...})`), `useForm({ resolver: zodResolver(schema), mode: 'onTouched' })`.
- Разметка — `Form/FormField/FormItem/FormLabel/FormControl/FormMessage` shadcn; у каждого инпута `name`, семантический `type` (`email/tel/url/number`) и `autocomplete` (`off` для не-auth полей); paste не блокировать; `spellCheck={false}` на email/кодах/ИНН.
- Ошибки — inline под полем (`FormMessage`, `text-status-danger-deep text-xs`); при submit — фокус на первое поле с ошибкой (`setFocus`). Ошибка 422 API: маппинг `loc → field` (существующий хелпер) → `form.setError(field, { message: detail })`, непривязанные — toast.
- Submit-кнопка активна до старта запроса; во время — спиннер `Loader2 animate-spin` + текст «Сохраняем…» (`aria-live="polite"` на статус).
- Плейсхолдеры — образец с `…`: «ИНН, 10 цифр…».

---

## 6. Группы страниц: что меняется

Общий принцип «меньше текста»: абзацы → строки фактов с иконками; подписи-объяснения → тултипы у иконки `Info` 14px; любые списки свойств → `DL`-сетка или бейджи; каждый пустой экран — иллюстрация + ≤ 1 предложение + CTA.

### 6.1 Логин (`LoginPage.tsx`)

Сплит-лейаут вместо одинокой карточки: слева (скрывается < 900px) — панель `bg-sidebar` с иллюстрацией `login-hero`, названием «CRM Вузы» и одной строкой «Партнёрства с вузами: заявки, workflow, talent pool» (`text-sidebar-muted`); справа на `bg-background` — карточка 360px: логотип, «Вход», кнопка `lg` «Войти через Keycloak» (иконка `LogIn`), подпись «Единый вход организации». Вход элементов — stagger 0.06. Сплеш `AuthSplash` — логотип с мягкой пульсацией opacity (reduce → статично); ошибка Keycloak — `ErrorState` c `error-broken`.

### 6.2 Каркас (`AppLayout`, `HeaderBar`, `SiderLogo`)

- Сайдбар: `bg-sidebar`, свой nav на `menuConfig` (замена `Menu`): пункт — `flex gap-3 rounded-md px-3 py-2 text-sidebar-muted hover:bg-white/5 hover:text-white`, активный — `bg-sidebar-active text-white` + скользящая подложка `layoutId="nav-active"` (`springs.snappy`); иконки lucide 18px (маппинг пунктов: Дашборд `LayoutDashboard`, Канбан `KanbanSquare`, Заявки `FileText`, Реестры `Library`, Конструктор `Workflow`, Импорт `FileUp`, Talent pool `GraduationCap`, Нотификации `Bell`, Админ `Shield`). Группы — `Collapsible`. Свёрнутый (64px) — иконки + `Tooltip side="right"`. Тач-цели ≥ 40px.
- Хедер 56px `bg-card border-b`: крошки; поиск — кнопка-инпут «Поиск…  ⌘K» (`SearchCommand`); **кнопка перезапуска тура** `CircleHelp` (§7.6); колокольчик (поповер: 10 событий `Timeline`-компактно, пусто — `notifications-empty` compact); аватар-меню `DropdownMenu`. Observer — постоянный бейдж `StatusBadge status="draft" icon={Eye}` «Режим просмотра».
- Контент — `PageTransition` (§2.3) вокруг `<Outlet/>`.

### 6.3 Дашборд

- KPI-ряд → 4 `StatCard` (тона: primary/success/primary/warning-«зависшие» с иконкой Flame), stagger, count-up.
- Панель фильтров — одна строка над сеткой (`SegmentedControl` B2B/B2C, период-пресеты + RangePicker, комбобоксы КАМ/вуз/продукт, справа «Сбросить» ghost + «Экспорт» `DropdownMenu`); активные фильтры видны чипами в триггерах.
- Виджеты — карточки `shadow-card` c заголовком 16/24 и иконкой-камерой (`ImageDown`, `aria-label="Скачать PNG"`); сетка `grid grid-cols-12 gap-4` (воронка 5 кол, динамика 7; на планшете всё 12). Тема `crm` §2.6, вход §2.3. Drill-down-курсор `cursor-pointer` + тултип «Открыть на канбане».
- Пустой виджет — `EmptyState compact` (`search-empty`), ошибка виджета — compact `error-broken` + «Повторить».
- Убрать текст: подписи-легенды не дублировать заголовком; период и скоуп читаются из панели, не из подзаголовков.

### 6.4 Канбан + карточка заявки

- Колонки: фон `bg-muted/60 rounded-lg`, ширина 300px, заголовок — точка цвета этапа 8px + имя + счётчик `Badge` + сумма `tabular-nums`; терминальные — «стопки» 48px (вертикальный текст), разворачиваются с `springs.gentle`.
- Карточка (`BoardCard`): `bg-card rounded-md shadow-card p-3`, слева тонкая полоса 3px цвета этапа; title `truncate font-medium`; продукт/`b2c`-тип — `StatusBadge sm`; низ — `UserAvatar` 24px + «N дн.» `text-xs text-muted-foreground`; зависшая — бейдж `Flame` warning; несинхронизировано — иконка `Plug` c тултипом. Hover/drag/drop-анимации — §2.3. dnd-kit-логика (подсветка разрешённых колонок, optimistic, rollback, 409) не меняется.
- Карточка заявки: `Sheet` 720px (кнопка `ExternalLink` «Открыть отдельно»). Шапка-«паспорт» вместо текстовой простыни: строка бейджей (этап `StageTag`, «зависла», продукт) + сетка фактов 2×2 с иконками (`Building2` контрагент-ссылка, `UserRound` ответственный-Select, `CalendarPlus` создана, `RefreshCw` обновлена). «Перевести на этап ▾» — `DropdownMenu` (возвраты — danger-подсекция «↩ Возврат на…», с `requires_comment` → `ConfirmDialog` с textarea причины).
- Табы Таймлайн/Комментарии/Файлы — `Tabs`; таймлайн §3.7 с фильтр-чипами; комментарии — пузыри `bg-muted rounded-lg` (свои — `bg-primary-tint`), `MentionsTextarea`, optimistic-серый; файлы — `FileDropzone` + список строк (иконка типа, имя `truncate`, размер, автор — meta в `text-xs`).
- Правая колонка — блоки «Связи» (мини-карточки вуза/договора: две строки + иконка, стрелка `ChevronRight`), «Атрибуты» (`InlineEdit`), «Интеграции» (строки `Check/RotateCw/AlertTriangle` + текст, кнопка «Повторить» для admin). На планшете — всё в табы.

### 6.5 Реестры + детальные

- Все 4 реестра — миграция на `DataTable` заменой импорта; колонки-объявления правятся только в типе (`sorter: true` вместо AntD-`sorter`). Пустые: `registry-empty` + «Создать» + «Импортировать из Excel».
- Ячейки «оживить»: название — ссылка `text-primary hover:underline`; счётчики заявок — `Badge`; истекающий договор — строка `bg-status-warning-tint` + бейдж «< 30 дней»; активность продукта — `Switch` прямо в ячейке.
- Приоритет программ: первая колонка — drag-handle `GripVertical` + номер в кружке `bg-primary-tint`; строки — dnd-kit sortable (перенос `springs.release`); ▲▼ — icon-кнопки с `aria-label`. Тумблер «Ручной приоритет / По заявкам» — `SegmentedControl`.
- Детальные страницы: `PageHeader` с `backTo` и `meta`-бейджами; реквизиты — `DL`-сетка §5.1 (не Descriptions-простыня); правая колонка — мини-карточки связей как в 6.4.

### 6.6 Конструктор workflow + мастер импорта

- Канва: фон точки `#DDE4EE`, узел этапа — карточка 200×72 `bg-card rounded-md shadow-card`, полоса 4px цвета этапа слева, имя `font-medium truncate`, низ — «заявок: N» `text-xs text-muted-foreground` + иконки-маркеры lucide (`Flag` начальный, `Target` терминальный, `MessageSquareText` требует комментария); выбранный — `ring-2 ring-primary`; появление узла — scale 0.95→1 `springs.gentle`. Рёбра: обычное — `#1E56A0` 1.5px, возврат — `#C5372B` dashed дугой с меткой «возврат». Черновик: рамка канвы `border-2 border-status-warning` + водяной бейдж «ЧЕРНОВИК» warning-tint (вместо жёлтой заливки).
- Панель свойств справа — карточка с формой §5.2; выбор цвета — 8 свотчей §1.2 (кнопки 24px, `aria-label` с названием цвета, выбранный — ring). Сводка схемы (ничего не выбрано) — StatCard-мини «этапов/переходов/заявок» + список ошибок валидации (строки `AlertTriangle` danger-deep, клик — фокус узла).
- Красный сценарий удаления — `ConfirmDialog` §3.8: facts-плашки («4 заявки», «переходов: 2 вх / 3 исх»), radio миграции (варианты с метками «← предыдущий»/«→ следующий», дефолт предыдущий), `confirmWord` = имя этапа. Призрак удалённого узла — `opacity-40 grayscale` + зачёркнутое имя + кнопка «Восстановить».
- Impact-preview — `Dialog` с диффом: строки added/renamed/deleted как плашки `success/warning/danger-tint` с иконками `Plus/Pencil/Trash2` (не текстовый список).
- Мастер импорта: `Stepper` §3.10; шаг 1 — `FileDropzone` с иллюстрацией `import-drop` во всю ширину; шаг 2 — превью-таблица shadcn + селекты листа/кодировки (смена кодировки — мгновенная перерисовка, warning-alert при кракозябрах); шаг 3 — строки маппинга: поле CRM (звёздочка обязательности, тип-иконка) → `Combobox` колонки файла (пример значения в опции `text-muted-foreground`) → живой пример; авто-смапленные — бейдж «авто» `primary-tint`; секция «Не используются» — `Collapsible`. Шаг 4 — сводка 4 `StatCard`-мини (всего/пройдут/предупреждения/ошибки, тона draft/success/warning/danger) + таблица ошибок построчно (фильтр «только ошибки» — Switch) + radio политики дублей; прогресс импорта — `Progress`; финал — `EmptyState`-подобный success-экран с `import-done`, счётчиками и 3 кнопками.

### 6.7 Talent pool

- Воронка-переключатель — 4 крупных сегмента-карточки (счётчик 28px count-up + подпись), активный — заливка `talent-tint` + ring `talent`; соединены стрелками `ChevronRight text-border`; клик фильтрует (`springs.snappy` на подложке).
- Таблица — `DataTable`; маскированные ПДн — моноширинная маска `text-muted-foreground`; иконка `Eye` (kam+) с тултипом «Просмотр будет зафиксирован в журнале аудита»; раскрытые значения — подсветка `talent-tint` + таймер-точка 60с (анимация масштаба точки; reduce — статичная) → обратная маскировка fade.
- Карточка студента: шапка-паспорт как 6.4; табы Профиль (DL-сетка + бейджи федпроектов)/Расписание/Файлы/История (`Timeline`). Observer — плашка `alert` «ПДн скрыты согласно роли» с иконкой `EyeOff`.
- Пусто — `students-empty` + CTA «Импортировать список».

### 6.8 Нотификации

- Карточки каналов — 3 в ряд: иконка канала в tint-квадрате (`Send` TG / `MessageCircle` Max / `Mail` Email), статус — `StatusBadge` («Привязан: @ivanov_kam» success / «Не привязан» draft / «заглушка» warning), Switch, кнопка. Пустой TG — компакт-иллюстрация `notifications-empty`.
- Матрица подписок — таблица shadcn: строки-события с иконками типов, чекбоксы по каналам; шапка sticky.
- «Правила и пороги» — карточка с формой §5.2; схема эскалации — **мини-SVG-диаграмма** (узлы «КАМ → 🔥N дн. → Руководитель» в стиле §4) вместо текстового описания; тех-справка — `Collapsible`.
- Модалка привязки TG: QR крупно (canvas `qrcode`), код 6 цифр `text-[28px] tabular-nums tracking-[0.3em]` + кнопка `Copy` («Скопировано» — toast), таймер TTL `Progress` тонкой полоской; по SSE `telegram.linked` — контент модалки сменяется галкой `Check` в success-tint круге (scale `springs.bouncy`) и автозакрытие через 1.2с.

### 6.9 Админ-зона

- Все таблицы (users, audit, журнал обмена) — `DataTable`; audit: быстрый чип-фильтр «Раскрытия ПДн» — `StatusBadge talent` кликабельный.
- `/admin/approvals`: входящие — карточки со строкой автор+дата+workflow, диффом-плашками (как impact-preview 6.6) и парой кнопок «Согласовать и опубликовать» primary / «Вернуть» outline+textarea; пусто — иллюстрация `registry-empty` с текстом «Согласований нет».
- Фичефлаги — строки-карточки: имя `font-mono text-sm`, описание одной строкой, Switch, «изменён кем/когда» `text-xs text-muted-foreground`; `llm_features` — disabled c подписью-тултипом.
- Интеграции: две карточки-коннектора со статус-точкой (пульс `animate-pulse` у живого; reduce — статично) и счётчиками «→ N / ← M» `tabular-nums`; журнал — DataTable, направление — иконка `ArrowRight`/`ArrowLeft` в tint-круге, кнопка `{}` — `Dialog` с `<pre>` JSON (`bg-sidebar text-sidebar-muted rounded-md p-4 text-xs`).

---

## 7. Онбординг сотрудников (новая фича)

### 7.1 Архитектура

`src/features/onboarding/`: `OnboardingProvider` (контекст: состояние, `startTour(tourId)`, `completeChecklistItem(id)`), `WelcomeDialog`, `SpotlightTour`, `ChecklistCard`, `tours.ts` (конфиги по ролям). Реализация **своя**: portal + spotlight + motion (Р10).

**Хранение прогресса** — существующий API `/api/v1/ui/presets` (per-user, переживает смену браузера): один пресет `screen: "onboarding"`, `name: "state"`, state:

```json
{
  "welcome_seen": true,
  "tours": { "kam-main": "done" },        // done | skipped
  "checklist": { "open-board": true, "save-preset": false },
  "version": 1                             // рост версии → предложить тур заново
}
```

Загрузка при старте приложения (после auth, параллельно с прочим); запись — `PATCH` c debounce 1с, optimistic. Роль пользователя определяет набор туров/чек-листа: `admin > head_kam > kam > observer` (берётся старшая роль).

### 7.2 Welcome-экран (первый вход)

`Dialog` 560px, показывается один раз (`welcome_seen: false`), закрытие любым способом ставит `true`. Состав: иллюстрация `onboarding-welcome`, заголовок «Добро пожаловать в CRM Вузы, {имя}!», один абзац по роли, 3 плашки «что вы можете» (иконка + 3–4 слова), кнопки: primary «Начать тур (≈1 мин)» → `startTour(mainTourId)`, ghost «Позже» (тур останется на кнопке `?` в шапке). Вход плашек — stagger `springs.bouncy`.

Тексты по ролям (заголовок плашек — иконка lucide):

- **kam**: «Здесь вы ведёте заявки своих вузов по этапам, импортируете данные из Excel и следите, чтобы ничего не зависло.» Плашки: `KanbanSquare` «Канбан заявок» · `FileUp` «Импорт из Excel» · `Bell` «Уведомления в Telegram».
- **head_kam**: «Вам видны заявки всей команды: дашборд, настройка workflow и приоритеты программ.» Плашки: `LayoutDashboard` «Дашборд команды» · `Workflow` «Правки workflow» · `GraduationCap` «Talent pool».
- **admin**: «Вы управляете системой: схемы workflow, согласования, пользователи и мониторинг интеграций.» Плашки: `Workflow` «Конструктор» · `ShieldCheck` «Согласования» · `Plug` «Интеграции».
- **observer**: «Вам доступен просмотр всех данных без изменений; ПДн студентов маскированы.» Плашки: `Eye` «Режим просмотра» · `LayoutDashboard` «Дашборды» · `Download` «Экспорт».

### 7.3 Spotlight-тур: реализация

- Целевые элементы размечаются атрибутом `data-tour="<step-id>"` (стабильные id, не CSS-селекторы по классам).
- `SpotlightTour` рендерится в portal: полноэкранный оверлей `position: fixed; inset: 0; z-50`; подсветка — **один div-«дыра»**: `position: fixed`, геометрия = `getBoundingClientRect()` цели + 8px паддинг, `border-radius: 12px`, `box-shadow: 0 0 0 9999px oklch(0.267 0.069 258.9 / 0.55)` (затемнение всего, кроме выреза) + `ring-2 ring-primary`. Переход между шагами — motion `layout`-анимация этого div (`springs.gentle`): рамка «перелетает» к следующей цели.
- Карточка шага 320px — `position: fixed` рядом с вырезом: плейсменты `bottom | top | right | left`, автофлип при нехватке места, clamp в вьюпорт (ручной расчёт, без сторонних либ). Состав: счётчик «Шаг 2 из 7» `text-xs text-muted-foreground`, заголовок 16/24 semibold, текст ≤ 2 строк, точки-прогресс, кнопки «Назад» ghost / «Далее» primary (на последнем — «Готово»), крестик = «Пропустить тур» (`tours[id]='skipped'`).
- Если шаг требует другого маршрута — тур сам делает `navigate(route)` и ждёт появления `data-tour`-цели (poll через `MutationObserver`/raf, таймаут 3с → шаг пропускается).
- A11y: карточка — `role="dialog" aria-modal="true"`, focus-trap внутри карточки, Esc = пропустить, стрелки ←/→ = навигация; фон под оверлеем `inert`; при reduced-motion рамка перемещается без анимации.
- Скролл к цели: `scrollIntoView({block:'center', behavior: reduce ? 'auto' : 'smooth'})`.

### 7.4 Шаги туров (тексты — финальные)

**Тур `kam-main` (7 шагов):**

| # | Цель `data-tour` | Маршрут | Заголовок / текст |
|---|---|---|---|
| 1 | `nav` | /board | «Навигация» / «Слева — все разделы, доступные вашей роли. Начнём с главного — канбана заявок.» |
| 2 | `board-wf-switch` | /board | «Два процесса» / «B2B — работа с вузами, B2C — с физлицами и юрлицами. Доска показывает актуальную схему.» |
| 3 | `board-card` (первая карточка) | /board | «Заявка» / «Тяните карточку между этапами. Подсветятся колонки, куда переход разрешён, — возвраты тоже.» |
| 4 | `board-filters` | /board | «Фильтры и пресеты» / «Настройте фильтры под себя и сохраните как пресет — он будет ждать вас при следующем входе.» |
| 5 | `nav-import` | /board | «Импорт из Excel» / «Загружайте XLSX и даже старый XLS: мастер сам предложит маппинг колонок и покажет ошибки построчно.» |
| 6 | `header-bell` | /board | «Уведомления» / «Здесь события по вашим заявкам. Привяжите Telegram — важное придёт прямо в чат.» |
| 7 | `header-search` | /board | «Быстрый поиск» / «Cmd+K — и вы найдёте вуз, заявку или студента, не отходя от кассы.» |

**Тур `admin-main` (7 шагов):** 1 `nav` («Вам доступна вся система, включая раздел Админ») → 2 `wf-canvas` /workflow («Конструктор: этапы и переходы — это карточки и стрелки. Тащите, соединяйте, пробуйте») → 3 `wf-properties` («Панель свойств: имя, цвет, пороги зависания и роль этапа») → 4 `wf-submit` («Опасные правки — переименование и удаление этапа — всегда идут через согласование, даже ваши») → 5 `admin-approvals` /admin/approvals («Здесь вы одобряете пакеты изменений: дифф, миграция заявок, публикация в один клик») → 6 `admin-integrations` /admin/integrations («Монитор LMS/CMS: журнал обмена в обе стороны и тестовые события») → 7 `admin-audit` /admin/audit («Журнал аудита: каждый вход, переход и раскрытие ПДн — с фильтрами»).

**Тур `head-main` (6 шагов):** дашборд-фильтры («видите всю команду; клик по графику ведёт в отфильтрованный канбан») → канбан («вам доступен drag любых заявок и смена ответственных») → конструктор-черновик («правьте схему в черновике — на работу команды это не влияет») → отправка на согласование («пакет уйдёт админу; статус видно здесь») → talent pool («воронка студентов: от кандидата до talent pool») → пороги эскалации /settings/notifications («настройте, когда заявка считается зависшей и когда эскалировать»).

**Тур `observer-main` (4 шага):** бейдж «Режим просмотра» → дашборд-экспорт («экспортируйте PNG и XLSX — просмотр не ограничивает выгрузку») → реестры → маскирование ПДн («персональные данные студентов всегда маскированы для вашей роли»).

### 7.5 Чек-лист «Начало работы»

`ChecklistCard` — карточка на дашборде (первая позиция сетки, пока не завершён): заголовок «Начало работы», `Progress` «3 из 6», строки: иконка-круг → `Check` в success при выполнении (перечёркивание текста, motion path/scale `springs.bouncy`), клик по строке ведёт в нужный экран. Выполнение отмечается **автоматически** хуками в соответствующих местах (`completeChecklistItem(id)` идемпотентен). Кнопка «Скрыть» (`DropdownMenu`) — скрывает навсегда (в state). По достижении 100% — карточка сменяется поздравлением с иллюстрацией `onboarding-welcome` и кнопкой «Скрыть» (+ конфетти не делаем).

Пункты kam: `tour` Пройти тур · `open-request` Открыть карточку заявки · `move-request` Перевести заявку по этапу · `save-preset` Сохранить пресет таблицы · `link-telegram` Привязать Telegram · `run-import` Выполнить импорт (можно демо-файлом). head_kam: тур · дашборд с фильтром по КАМу · черновик workflow · отправить на согласование · пороги эскалации. admin: тур · открыть согласования · переключить фичефлаг · тестовое событие интеграции · открыть аудит. observer: тур · экспорт виджета · открыть talent pool.

### 7.6 Перезапуск тура

Кнопка `CircleHelp` в шапке (`aria-label="Помощь и туры"`) — `DropdownMenu`: «Пройти тур по разделу» (пункты = туры роли; текущий раздел — первым), «Показать чек-лист» (если скрыт — возвращает), «Приветственный экран». Перезапуск не сбрасывает чек-лист.

---

## 8. Ограничения, a11y, бандл, приёмка

### 8.1 Адаптив (до планшета, ux.md §4.3 остаётся в силе)

Desktop ≥1200 — полный layout; 992–1199 — сайдбар в иконки; 768–991 — сайдбар-`Sheet` по бургеру, канбан — горизонтальный скролл + scroll-snap, `DataTable` скрывает колонки `responsive`, карточка заявки — табы, конструктор — read-only-предупреждение. Тач: dnd-kit `TouchSensor` long-press 250ms (не менять), тач-цели ≥ 40×40px, hover-эффекты за `@media (hover: hover)`.

### 8.2 Чек-лист a11y (из web-interface-guidelines — проверяется на ревью каждого PR)

- Фокус: `:focus-visible`-кольцо из §1.1 на всём интерактивном; `outline-none` без замены запрещён; sticky-хедер не перекрывает фокусируемое.
- Семантика: действия — `<button>`, навигация — `<Link>`; `div onClick` запрещён; иконочные кнопки — `aria-label`; декоративные иконки/SVG — `aria-hidden="true"`; заголовки иерархичны (один h1 на экран).
- Live-регионы: тосты (sonner сам), «Сохраняем…», счётчик результатов поиска — `aria-live="polite"`.
- Контраст: только пары из §1.2; новые сочетания — считать перед использованием (порог 4.5 текст / 3.0 крупный текст и графика).
- Таблицы: `aria-sort` на сортируемых, `scope="col"`; числа `tabular-nums`.
- Формы — §5.2 целиком; чекбокс/радио — общий hit-target с label.
- Списки > 50 строк на клиенте (журнал обмена live) — `content-visibility: auto`.
- Motion — §2.4.

### 8.3 Бандл и производительность

- Lazy-чанки сохраняются: `/dashboard` (echarts), `/workflow` (+`@xyflow/react`), `/import`; `React.lazy` + `Suspense` со скелетоном экрана. `manualChunks`: `vendor-echarts`, `vendor-flow`, `vendor-motion`.
- ECharts — модульный импорт (`echarts/core` + используемые charts/components), не полный пакет.
- Иллюстрации — инлайн-компоненты в чанке экрана, не в общем vendor.
- Ожидание: удаление antd (~1.2MB parsed) компенсирует motion+radix+tanstack с запасом; общий initial-чанк не должен вырасти (проверить `vite build` до/после).
- Никаких CDN/внешних запросов: шрифты/иконки/стили — из бандла (проверка: DevTools Network offline — приложение живо).

### 8.4 Запреты

Тёмная тема; сторонние тур-библиотеки; `framer-motion`-импорт (только `motion/react`); hex в компонентах; `transition: all`; анимация layout-свойств; эмодзи в UI; потеря любых функций ux.md (API-слой, keycloak, SSE, optimistic-паттерны, RBAC-guard'ы — не трогаем).

---

## 9. Разбиение на 6 параллельных пакетов работ

| WP | Состав | Зависимости |
|---|---|---|
| **WP1 Фундамент** | Tailwind v4 + globals.css (§1), shadcn-база (§3.1), `motion.ts` (§2.1), `tokens.ts`, тема ECharts `crm` (§2.6), `PageTransition`, sonner, `useBreakpoint`, замена шрифта | — (стартует первым, остальные — от его ветки; срок ≤ 1 дня) |
| **WP2 DataTable + реестры + admin-таблицы** | §3.2, PresetsControl, миграция 4 реестров + детальных (§6.5), admin users/audit/журнал (§6.9-таблицы) | WP1 |
| **WP3 Shell + логин + дашборд** | §6.1, §6.2, §6.3, `StatCard`, `SearchCommand`, колокольчик | WP1 |
| **WP4 Канбан + карточка заявки** | §6.4, `Timeline`, `InlineEdit`, `MentionsTextarea`, `FileDropzone`(общий) | WP1 |
| **WP5 Конструктор + импорт + approvals** | §6.6, `ConfirmDialog`, `Stepper`, impact-preview, §6.9-approvals | WP1 (FileDropzone из WP4 — интерфейс зафиксирован §3.9, можно параллельно по контракту) |
| **WP6 Talent pool + нотификации + иллюстрации + онбординг** | §6.7, §6.8, все 12 SVG (§4.2), §7 целиком | WP1; `data-tour`-атрибуты в чужих экранах — PR-ами в ветки WP2–WP5 по списку §7.4 |

Общие компоненты §3 закреплены за WP владельца первого употребления (указано выше); контракты пропсов из этого документа — API-заморозка: менять контракт можно только правкой этого файла.
