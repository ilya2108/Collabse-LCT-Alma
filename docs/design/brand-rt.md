# Бренд-ресёрч: дизайн-система Ростелекома «2-е поколение» (Атомаро)

План ретеминга токенов CRM под брендбук РТК. Все значения сняты 29.09.2026 с живых
источников (не из памяти):

| Источник | Что снято | Метод |
|---|---|---|
| design.rt.ru/gen2/basics/colors/palette | структура палитры (fg/bg/border/accent/neutral/success/warning/error/info/status-01…06/static), полные рампы 0–1000 | `document.styleSheets` → 274 литеральных значения `--atmr-*` |
| design.rt.ru/gen2/react-storybook, story `components-buttons-button--main`, globals `cssVariables: Rostelecom Light Theme` | светлые алиасы (`--atmr-accent-default` и др.), computed styles Button/Input, вся типографическая шкала, радиусы, тени, спейсинг, `@font-face` | JS в превью-iframe |
| rtkb.zion-lms.ru (референс LMS) + /user/login | фон, кнопки, карточки, шрифты | скриншоты + computed styles |

Все источники были доступны без VPN. Контрастные пары посчитаны скриптом (WCAG relative
luminance); цифры в таблицах — реальные ratio.

---

## 0. Ключевой факт: у «бренда» два лица

1. **ДС Ростелекома (Атомаро), тема «Rostelecom Light»**: акцент — **оранжевый
   `#FF4F12`** (primary-кнопка в Storybook реально оранжевая: bg `rgb(255,79,18)`,
   hover `#D9430F`, active `#B3370D`, on-accent `#FFF`), шрифт **Rostelecom Basis**,
   радиус базовый **8px**. Фиолетовый `#7700FF` — второй фирменный (base-accent
   в палитре доков; сам сайт design.rt.ru использует его в тёмной теме).
2. **Референс LMS rtkb.zion-lms.ru** («делаем по аналогии») — white-label Zion LMS
   с брендингом РТК: акцент **синий `#007CFF`**, шрифт **Inter**, фон `#F6F7FB`,
   белые карточки r16 с мягкой тенью.

Наша CRM уже синяя (`#1E56A0`) — это совпадает с LMS-референсом, а не с оранжевой
темой ДС. **Решение**: primary остаётся синим, но пересаживается на фирменный
info-рамп ДС РТК (`base-info #1F69FF`); оранжевый `#FF4F12` вводится как брендовый
акцент второго плана (иллюстрации, маркетинговые моменты), фиолетовый рамп — Talent
Pool. Так мы «по аналогии» с LMS и одновременно все значения — из палитры РТК.
LMS-синий `#007CFF` не годится в primary напрямую: белый текст на нём 3.94:1
(< AA 4.5).

---

## 1. Палитра РТК (сырьё, sourced)

### 1.1 Базовые цвета (`--atmr-base-*`, палитра доков)

| Токен ДС | HEX | Роль |
|---|---|---|
| base-accent | `#7700FF` | фирменный фиолетовый |
| accent-400 | `#9233FF` | фиолетовый рабочий (контейнеры rgba(146,51,255,…)) |
| base-info | `#1F69FF` | фирменный синий |
| base-success | `#0ACB5B` (рамп) / `#00AC43` (light-алиас success-default) | зелёный |
| base-warning | `#FDA610` | янтарный |
| base-error | `#FF2626` (рамп) / error-600 `#D92020` | красный |
| base-neutral | `#585D69` | нейтральный |
| status-01 | `#FF4F12` | **фирменный оранжевый РТК** |
| status-02 | `#4055E8` (рамп 400 `#6677ED`) | индиго |
| status-03 | `#038FDE` (рамп 400 `#35A5E5`) | голубой |
| status-04 | `#1898A9` (рамп 400 `#46ADBA`, 600 `#148190`) | бирюзовый |
| status-05 | `#CA20D9` (рамп 400 `#D54DE1`) | фуксия |
| status-06 | `#D9206F` (рамп 400 `#E14D8C`, 600 `#B81B5E`) | розовый |

Каждый цвет — рамп из 17 ступеней (0,10,25,50,100…990,1000); ступени, которые
используются ниже, выписаны точно (полный дамп — 274 значения, снят целиком).

### 1.2 Светлые алиасы (Storybook, Rostelecom Light Theme, computed)

| Алиас ДС | Значение |
|---|---|
| fg-default | `#101828` (= neutral-990) |
| fg-soft / fg-muted | `rgba(16,24,40,.75)` / `rgba(16,24,40,.55)` |
| bg-page | `#FFF`; bg-page-filled `#F4F4F5` |
| border default/soft/muted | `rgba(88,93,105, .5 / .25 / .15)` |
| accent default/hover/active | `#FF4F12` / `#D9430F` / `#B3370D`, on-accent `#FFF` |
| success/warning/error/info/neutral default | `#00AC43` / `#FDA610` / `#FF2626` / `#1F69FF` / `#585D69` |
| контейнеры статусов | цвет с альфой .05/.1/.2/.3/.4 (muted/soft/default/hover/active) |
| focus | `rgba(<accent>, .2)` |

### 1.3 Характер референса LMS (5–7 черт, computed + скриншоты)

1. Светлый фон `#F6F7FB`, белые карточки **r16** с тенью `0 8px 16px rgba(0,0,0,.08)` — воздушно, без рамок.
2. Акцент — яркий синий: кнопки `#007CFF` (r6, 600), ссылки `#1A96F6`; широкие синие промо-блоки со скруглёнными светлыми кнопками.
3. Тёмный (почти чёрный `#1A1A1A`-графит) футер/тех-блоки — тёмная зона в light-интерфейсе легитимна (наш тёмный сайдбар вписывается).
4. Типографика Inter (h1 32/40 600, текст 16/24), плотность невысокая, крупные отступы.
5. Никаких градиентов в UI-слое; цвет — плоский, эмоция — за счёт иллюстраций и фото.
6. Заголовочный текст тёмно-графитовый `#333`, не чисто чёрный.

---

## 2. Маппинг на наши токены `frontend/src/styles/globals.css`

Формат: переменная → новое значение (все HEX — из рампов §1; OKLCH пересчитать при
правке, hex держать в комментарии, как сейчас).

### 2.1 Поверхности и текст

| Наш токен | Было | Станет | Источник/контраст |
|---|---|---|---|
| `--background` | `#F4F6FA` | `#F6F7FB` | фон LMS (sampled); альтернатива из ДС — neutral-10 `#F9F9FA` |
| `--card`, `--popover` | `#FFFFFF` | без изменений | bg-elevated-0 = #FFF |
| `--foreground` | `#1F2733` | `#101828` | fg-default РТК (17.75:1 на белом) |
| `--muted` | `#EEF2F8` | `#F4F4F5` | neutral-25 (= bg-page-filled) |
| `--muted-foreground` | `#5A6B80` | `#585D69` | base-neutral, **6.59:1** на белом — AA |
| `--border`, `--input` | `#DDE4EE` | `#E8E8EE` | neutral-50 (ДС использует альфы от #585D69; для нас — непрозрачный эквивалент из рампа) |

### 2.2 Бренд

| Наш токен | Было | Станет | Контраст |
|---|---|---|---|
| `--primary` | `#1E56A0` | `#1F69FF` (base-info, «синий РТК») | белый на нём **4.64:1** — AA |
| `--primary-hover` | `#2E68B8` | `#1A59D9` (info-600) | 6.04:1 |
| `--primary-active` | `#174682` | `#164AB3` (info-700) | 7.88:1 |
| `--primary-tint` | `#EEF3FB` | `#F4F8FF` (info-25) | hover строк |
| `--primary-tint-2` | `#DCE7F5` | `#E9F0FF` (info-50) | активные плашки |
| `--ring` | = primary | `#1F69FF` | фокус в ДС — альфа .2 от акцента; наше кольцо 2px непрозрачное — ок |
| `--sidebar` | `#0F2547` | `#151D2C` (neutral-950, графит РТК — как тёмные зоны LMS) | белый 16.88:1 |
| `--sidebar-muted` | `#A9BBD6` | `#9DA1AC` (neutral-300) | **6.53:1** на #151D2C — AA |
| `--sidebar-active` | = primary | `#1F69FF` | на #151D2C: 3.0:1 — использовать как заливку активного пункта с белым текстом (4.64:1), не как текст |
| `--secondary` | `#EEF2F8` | `#F4F4F5` | = muted |
| `--accent` / `--accent-foreground` | tint / `#174682` | `#F4F8FF` / `#164AB3` | |
| `--destructive` | `#C5372B` | `#D92020` (error-600) | белый 5.03:1 |
| Новый (опц.) `--brand-orange` | — | `#FF4F12` | фирменный оранжевый: логотип-моменты, онбординг-иллюстрации; текстом не давать (3.29:1) |

### 2.3 Статусные тройки core/deep/tint (семантика воронки сохраняется)

Все deep-на-tint пары посчитаны, AA ≥ 4.5 везде:

| Статус | core (маркеры/графики) | deep (текст) | tint (фон бейджа) | deep/tint |
|---|---|---|---|---|
| draft | `#585D69` (base-neutral) | `#454B59` (neutral-700) | `#F4F4F5` (neutral-25) | **7.95** |
| progress | `#1F69FF` (base-info) | `#164AB3` (info-700) | `#E9F0FF` (info-50) | **6.90** |
| success | `#09AD4D` (success-600) | `#067A37` (success-800) | `#E7FAEF` (success-50) | **5.02** |
| warning | `#D78D0E` (warning-600) | `#98640A` (warning-800) | `#FFF6E7` (warning-50) | **4.71** |
| danger | `#D92020` (error-600) | `#B31B1B` (error-700) | `#FFE9E9` (error-50) | **5.85** |
| talent | `#9233FF` (accent-400) | `#6500D9` (accent-600) | `#F1E6FF` (accent-50) | **6.85** |

Замечания: success-700 `#078E40` на тинте даёт только 3.9 — поэтому deep взят 800;
warning аналогично (700 → 3.65, 800 → 4.71). Core-цвета — только маркеры/полосы
(правило tokens.ts сохраняется). Синхронно править `STATUS_TRIPLES` в
`frontend/src/shared/config/tokens.ts` и OKLCH-тройки в globals.css.

### 2.4 Геометрия и тени

Радиусы ДС: 2xs 2 / controls 3 / xs 4 / s 6 / **base(m) 8** / l 12 / xl 16 / 2xl 32 / full.
Кнопки и инпуты ДС = m (8px, подтверждено computed: Button r8 h36, Input r8 h36 border 2px).

- `--radius: 0.625rem` (10px) **оставить**: производные sm 6 / md 8 / xl 16
  бит-в-бит совпадают с s/m/xl ДС; lg 10 — компромисс между base 8 и LMS-карточками 16.
  Крупные «витринные» карточки (LMS-стиль) переводить на `rounded-xl` (16px) точечно.
- Тени → рецепты ДС (двухслойные, цвет `rgba(88,93,105,…)` вместо наших oklch-теней):
  - `--shadow-card` → shadow-bottom-s: `0 0 8px rgba(88,93,105,.1), 0 2px 4px rgba(88,93,105,.05)`
  - `--shadow-overlay` → shadow-bottom-l: `0 0 20px rgba(88,93,105,.1), 0 12px 20px rgba(88,93,105,.05)`
  - `--shadow-drag` → shadow-bottom-xl: `0 0 32px rgba(88,93,105,.1), 0 32px 32px rgba(88,93,105,.05)`
  - LMS-вариант карточной тени `0 8px 16px rgba(0,0,0,.08)` — допустимая
    альтернатива для промо-блоков.
- Спейсинг ДС — сетка 4px (`spacing-1x` 4 … base 16 … 48x 192) — наша Tailwind-сетка
  уже совместима, правок не требует.

---

## 3. Типографика

**Фирменный шрифт: Rostelecom Basis** (`--atmr-font-family-base: 'Rostelecom Basis'`,
весь UI ДС на нём). Начертания в ДС: 300/400/500/700.

woff2 отдаются публично со Storybook (проверено HEAD → 200, `font/woff2`):

- https://design.rt.ru/gen2/react-storybook/fonts/RostelecomBasis-Regular.woff2
- https://design.rt.ru/gen2/react-storybook/fonts/RostelecomBasis-Medium.woff2
- https://design.rt.ru/gen2/react-storybook/fonts/RostelecomBasis-Bold.woff2
- https://design.rt.ru/gen2/react-storybook/fonts/RostelecomBasis-light.woff2 (объявлен в @font-face, именно с маленькой «l»; HEAD не проверялся)

(рядом лежат .woff и .ttf с теми же именами; есть и Manrope-*.ttf)

**Фолбэк-решение (честно)**: шрифт проприетарный, лицензии на редистрибуцию у нас
нет — в публичную репу файлы не коммитим. Вариант А (демо): подключить @font-face
с этих URL + `font-display: swap`, fallback-стек `'Rostelecom Basis', 'Inter Variable',
'Segoe UI', system-ui, sans-serif`. Вариант Б (безопасный дефолт): Inter остаётся,
в README помечаем «в проде заменяется на Rostelecom Basis». Референс-LMS, кстати,
сам живёт на Inter — вариант Б тоже «по аналогии».

Шкала ДС (стиль: вес px/line-height, все — Rostelecom Basis):

| Роль | Значение |
|---|---|
| display l/m/s | 500 48/52 · 42/44 · 32/44 (strong 700) |
| h1…h5 | 700: 28/32 · 22/24 · 18/20 · 16/20 · 14/20 |
| body l/m/s | 400: 18/26 · 16/24 · **14/20** (strong 500) |
| description l/m/s | 400: 12/16 · 11/16 (+0.2px ls) · 10/12 (+0.4px ls) |

Наш body 14px = body-s ДС — совпадает, не трогаем. Заголовки страниц привести
к h2 22px, секции — h3 18px, вес 700 (сейчас semibold — поднять для заголовков).

---

## 4. Палитра графиков ECharts (в духе РТК)

6 категориальных, все из рампов ДС, контраст к белому ≥ 3:1 (посчитан), хью
разнесены (≈232° / 16° / 190° / 270° / 335° / 38°), пары риска при CVD
(оранжевый-охра, синий-фиолетовый) дополнительно разведены по светлоте:

| # | HEX | Источник | на белом |
|---|---|---|---|
| 1 | `#1F69FF` | base-info (= новый primary) | 4.64 |
| 2 | `#D9430F` | status-01-600 (оранжевый РТК, затемнён до контраста) | 4.41 |
| 3 | `#148190` | status-04-600 (бирюза) | 4.59 |
| 4 | `#9233FF` | accent-400 (фиолетовый) | 5.07 |
| 5 | `#B81B5E` | status-06-600 (малиновый) | 6.26 |
| 6 | `#B1740B` | warning-700 (охра) | 3.91 |

Правится `CHART_PALETTE` в tokens.ts (ECharts-тема подтянет автоматически).
Рамп воронки Talent Pool → фиолетовый рамп ДС: `['#9233FF','#A759FF','#BB80FF','#DDBFFF']`
(accent 400/300/200/100). `STAGE_COLOR_PRESET` (8 цветов) → шесть выше +
`#09AD4D` (success-600) и `#585D69` (base-neutral); функции stageTint/stageText
не меняются. В `echartsTheme.ts` обновится только `SURFACE` (импорт из tokens.ts).

---

## 5. Иллюстрации (`frontend/src/shared/illustrations/palette.ts`)

Дуотон пересаживается автоматически через var(), править только hex-фолбэки:

| Ключ ILL | Было | Станет |
|---|---|---|
| line | `#0F2547` | `#151D2C` |
| fill1 / fill2 | `#EEF3FB` / `#DCE7F5` | `#F4F8FF` / `#E9F0FF` |
| accent | `#1E56A0` | `#1F69FF` (точечно, ≤20% площади, можно `#FF4F12` как фирменный «огонёк» в 1–2 сюжетах онбординга) |
| talent | `#7A4FBF` | `#9233FF` |
| danger | `#C5372B` | `#D92020` |
| shadow | `#EEF2F8` | `#F4F4F5` |

---

## 6. Чек-лист файлов к правке

1. `frontend/src/styles/globals.css` — все токены §2 (OKLCH пересчитать из hex, hex в комментарии); шрифтовой стек §3.
2. `frontend/src/shared/config/tokens.ts` — `STATUS_TRIPLES`, `CHART_PALETTE`, `TALENT_FUNNEL_RAMP`, `STAGE_COLOR_PRESET`, `SURFACE` (все hex §2/§4).
3. `frontend/src/shared/config/echartsTheme.ts` — только fontFamily, если внедряем Rostelecom Basis; цвета придут из tokens.ts.
4. `frontend/src/shared/illustrations/palette.ts` — hex-фолбэки §5.
5. `frontend/index.html` или globals.css — @font-face Rostelecom Basis (вариант А) либо пометка в README (вариант Б).
6. `frontend/src/shared/config/theme.ts` (легаси AntD-мост) — проверить захардкоженные hex, синхронизировать с новым SURFACE.
7. README/скриншоты — перегенерировать после ретеминга.

## 7. Уверенность и ограничения

- **Высокая**: рампы `--atmr-*` (сняты дословно), светлые алиасы и computed styles
  Button/Input Storybook, типографическая шкала, радиусы/тени/спейсинг, URL шрифтов
  (Regular/Medium/Bold — HEAD 200), стили LMS (computed).
- **Средняя**: `--background #F6F7FB` — сэмпл LMS, а не токен ДС; выбор «синий
  вместо оранжевого» — наше продуктовое решение по LMS-референсу (задокументировано в §0).
- Тёмная тема ДС существует (bg-page → neutral-950 и т.д., дамп есть), но по Р9 у нас
  её нет — не переносим.
- Контраст-ratio считались скриптом по формуле WCAG; ΔE для CVD не считался численно —
  палитра §4 проверена по разбросу hue/lightness (пары риска разведены по светлоте).
