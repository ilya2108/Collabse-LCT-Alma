import type { ReactNode, SVGProps } from 'react';
import { ILL, STROKE_PROPS, SVG_PROPS } from './palette';

/**
 * 12 фирменных сюжетов (redesign.md §4.2): инлайн-SVG, дуотон бренда,
 * предметный минимализм на 8px-сетке, viewBox 240×160, один акцент на сюжет.
 * Употребляются через реестр <Illustration name="…"/> (индекс фичи).
 */

type Props = SVGProps<SVGSVGElement>;

/** Канбан-доска: 3 колонки, пунктирная карточка с плюсом, рука-курсор тянет карточку. */
export function BoardEmptyIllustration(props: Props): ReactNode {
  return (
    <svg {...SVG_PROPS} {...props}>
      <ellipse cx="120" cy="140" rx="88" ry="10" fill={ILL.shadow} />
      <g {...STROKE_PROPS}>
        <rect x="48" y="20" width="144" height="100" rx="8" fill={ILL.fill1} />
        <rect x="58" y="32" width="38" height="76" rx="4" fill={ILL.white} />
        <rect x="101" y="32" width="38" height="76" rx="4" fill={ILL.fill2} />
        <rect x="144" y="32" width="38" height="76" rx="4" fill={ILL.white} />
        <rect x="64" y="40" width="26" height="14" rx="3" fill={ILL.fill2} />
        <rect x="64" y="60" width="26" height="14" rx="3" fill={ILL.fill2} />
        <rect x="150" y="40" width="26" height="14" rx="3" fill={ILL.fill2} />
        <rect x="107" y="40" width="26" height="16" rx="3" fill={ILL.white} strokeDasharray="4 3" />
        <path d="M120 44v8M116 48h8" stroke={ILL.accent} />
      </g>
      <g {...STROKE_PROPS}>
        <rect x="150" y="104" width="34" height="16" rx="3" fill={ILL.white} />
        <path
          d="M178 116l4 12 3-5 5 3-4-6 5-2z"
          fill={ILL.accent}
          stroke={ILL.accent}
        />
      </g>
    </svg>
  );
}

/** Стопка карточек-папок с ярлыками, верхняя приоткрыта, акцентный ярлык. */
export function RegistryEmptyIllustration(props: Props): ReactNode {
  return (
    <svg {...SVG_PROPS} {...props}>
      <ellipse cx="120" cy="138" rx="84" ry="10" fill={ILL.shadow} />
      <g {...STROKE_PROPS}>
        <rect x="68" y="44" width="104" height="24" rx="5" fill={ILL.fill2} />
        <rect x="60" y="60" width="120" height="24" rx="5" fill={ILL.fill1} />
        <path d="M52 84h56l8-10h72v10z" fill={ILL.fill2} />
        <rect x="52" y="84" width="136" height="44" rx="6" fill={ILL.fill1} />
        <rect x="66" y="96" width="56" height="6" rx="3" fill={ILL.white} />
        <rect x="66" y="110" width="40" height="6" rx="3" fill={ILL.white} />
      </g>
      <rect x="150" y="92" width="26" height="12" rx="3" fill={ILL.accent} />
      <g {...STROKE_PROPS}>
        <rect x="84" y="34" width="20" height="10" rx="3" fill={ILL.white} />
      </g>
    </svg>
  );
}

/** Бейдж-пропуск студента с маскированными строками + академическая шапочка (talent). */
export function StudentsEmptyIllustration(props: Props): ReactNode {
  return (
    <svg {...SVG_PROPS} {...props}>
      <ellipse cx="120" cy="140" rx="80" ry="10" fill={ILL.shadow} />
      <g {...STROKE_PROPS}>
        <rect x="68" y="36" width="104" height="92" rx="8" fill={ILL.fill1} />
        <rect x="108" y="28" width="24" height="12" rx="4" fill={ILL.fill2} />
        <circle cx="94" cy="70" r="12" fill={ILL.fill2} />
        <rect x="116" y="58" width="42" height="6" rx="3" fill={ILL.white} />
        <rect x="116" y="72" width="30" height="6" rx="3" fill={ILL.white} />
        <rect x="82" y="96" width="76" height="6" rx="3" fill={ILL.fill2} />
        <rect x="82" y="110" width="52" height="6" rx="3" fill={ILL.fill2} />
      </g>
      <g stroke={ILL.talent} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M164 40l24 10-24 10-24-10z" fill={ILL.talent} />
        <path d="M150 52v10c0 4 6 8 14 8s14-4 14-8V52" fill="none" />
        <path d="M188 50v16" />
        <circle cx="188" cy="68" r="2" fill={ILL.talent} />
      </g>
    </svg>
  );
}

/** Лупа над листом с полосками, знак вопроса в линзе. */
export function SearchEmptyIllustration(props: Props): ReactNode {
  return (
    <svg {...SVG_PROPS} {...props}>
      <ellipse cx="120" cy="140" rx="80" ry="10" fill={ILL.shadow} />
      <g {...STROKE_PROPS}>
        <rect x="60" y="28" width="88" height="100" rx="6" fill={ILL.fill1} />
        <rect x="72" y="44" width="56" height="6" rx="3" fill={ILL.fill2} />
        <rect x="72" y="60" width="64" height="6" rx="3" fill={ILL.fill2} />
        <rect x="72" y="76" width="44" height="6" rx="3" fill={ILL.fill2} />
        <rect x="72" y="108" width="52" height="6" rx="3" fill={ILL.fill2} />
        <circle cx="148" cy="88" r="24" fill={ILL.white} />
      </g>
      <path
        d="M141 82a7 7 0 1 1 9 7c-2 1-2 2-2 4"
        stroke={ILL.line}
        strokeWidth="1.5"
        strokeLinecap="round"
        fill="none"
      />
      <circle cx="148" cy="99" r="1.5" fill={ILL.line} />
      <path d="M166 106l14 14" stroke={ILL.accent} strokeWidth="5" strokeLinecap="round" />
    </svg>
  );
}

/** Лист-таблица «влетает» в открытую папку, пунктирная траектория, стрелка вниз. */
export function ImportDropIllustration(props: Props): ReactNode {
  return (
    <svg {...SVG_PROPS} {...props}>
      <ellipse cx="120" cy="142" rx="84" ry="10" fill={ILL.shadow} />
      <g {...STROKE_PROPS}>
        <path d="M68 96h32l8 8h64v8H68z" fill={ILL.fill2} />
        <rect x="68" y="104" width="104" height="28" rx="4" fill={ILL.fill1} />
      </g>
      <g {...STROKE_PROPS} transform="rotate(8 148 44)">
        <rect x="124" y="24" width="48" height="40" rx="4" fill={ILL.white} />
        <path d="M124 36h48M124 48h48M140 24v40M156 24v40" stroke={ILL.fill2} />
        <rect x="124" y="24" width="48" height="40" rx="4" fill="none" />
      </g>
      <path
        d="M120 40c-24 4-40 24-42 48"
        stroke={ILL.line}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeDasharray="4 5"
        fill="none"
      />
      <path d="M100 66v22M92 80l8 10 8-10" stroke={ILL.accent} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" fill="none" />
    </svg>
  );
}

/** Лист с галкой в кружке-акценте, рядом стопка обработанных строк. */
export function ImportDoneIllustration(props: Props): ReactNode {
  return (
    <svg {...SVG_PROPS} {...props}>
      <ellipse cx="120" cy="140" rx="80" ry="10" fill={ILL.shadow} />
      <g {...STROKE_PROPS}>
        <rect x="56" y="88" width="40" height="8" rx="3" fill={ILL.fill2} />
        <rect x="52" y="100" width="48" height="8" rx="3" fill={ILL.fill1} />
        <rect x="88" y="28" width="72" height="96" rx="6" fill={ILL.fill1} />
        <rect x="100" y="44" width="48" height="6" rx="3" fill={ILL.white} />
        <rect x="100" y="60" width="36" height="6" rx="3" fill={ILL.white} />
        <rect x="100" y="76" width="44" height="6" rx="3" fill={ILL.white} />
      </g>
      <circle cx="156" cy="104" r="20" fill={ILL.accent} />
      <path
        d="M147 104l6 6 12-12"
        stroke={ILL.white}
        strokeWidth="3"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />
    </svg>
  );
}

/** Здание вуза с колоннами + щит с галкой (SSO), пунктирные узлы. */
export function LoginHeroIllustration(props: Props): ReactNode {
  return (
    <svg {...SVG_PROPS} {...props}>
      <ellipse cx="120" cy="140" rx="92" ry="10" fill={ILL.shadow} />
      <g {...STROKE_PROPS}>
        <path d="M64 56l56-28 56 28z" fill={ILL.fill1} />
        <rect x="68" y="56" width="104" height="10" rx="2" fill={ILL.fill2} />
        <rect x="76" y="66" width="12" height="42" fill={ILL.fill1} />
        <rect x="100" y="66" width="12" height="42" fill={ILL.fill1} />
        <rect x="124" y="66" width="12" height="42" fill={ILL.fill1} />
        <rect x="148" y="66" width="12" height="42" fill={ILL.fill1} />
        <rect x="68" y="108" width="104" height="8" rx="2" fill={ILL.fill2} />
        <rect x="60" y="116" width="120" height="8" rx="2" fill={ILL.fill1} />
      </g>
      {/* Флажок-«огонёк» на фронтоне — фирменный оранжевый РТК (brand-rt.md §5) */}
      <path d="M120 28V12" stroke={ILL.line} strokeWidth="1.5" strokeLinecap="round" />
      <path d="M120 12h13l-4 4.5 4 4.5h-13z" fill={ILL.orange} />
      <path
        d="M52 44c-8 6-14 18-14 30M196 40c8 10 12 22 12 34"
        stroke={ILL.line}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeDasharray="3 5"
        fill="none"
      />
      <g {...STROKE_PROPS}>
        <circle cx="36" cy="82" r="5" fill={ILL.fill2} />
        <circle cx="210" cy="82" r="5" fill={ILL.fill2} />
      </g>
      <path
        d="M176 84h32v16c0 10-7 17-16 20-9-3-16-10-16-20z"
        fill={ILL.accent}
        stroke={ILL.accent}
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
      <path
        d="M185 98l5 5 9-9"
        stroke={ILL.white}
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />
    </svg>
  );
}

/** Оборванный провод между узлами, искры (danger). */
export function ErrorBrokenIllustration(props: Props): ReactNode {
  return (
    <svg {...SVG_PROPS} {...props}>
      <ellipse cx="120" cy="136" rx="84" ry="10" fill={ILL.shadow} />
      <g {...STROKE_PROPS}>
        <circle cx="48" cy="88" r="16" fill={ILL.fill1} />
        <circle cx="48" cy="88" r="6" fill={ILL.fill2} />
        <circle cx="192" cy="88" r="16" fill={ILL.fill1} />
        <circle cx="192" cy="88" r="6" fill={ILL.fill2} />
        <path d="M64 88c16 0 24-8 34-6 6 1 10 4 12 8" fill="none" />
        <path d="M176 88c-14 0-22 8-32 6" fill="none" />
      </g>
      <g stroke={ILL.danger} strokeWidth="2" strokeLinecap="round">
        <path d="M116 72l4-8" />
        <path d="M126 70l7-6" />
        <path d="M132 100l6 7" />
        <path d="M122 104l2 8" />
        <path d="M138 84h9" />
      </g>
    </svg>
  );
}

/** Дверь с замком и тегом «наблюдатель» (danger-замок). */
export function Error403Illustration(props: Props): ReactNode {
  return (
    <svg {...SVG_PROPS} {...props}>
      <ellipse cx="120" cy="140" rx="72" ry="10" fill={ILL.shadow} />
      <g {...STROKE_PROPS}>
        <rect x="84" y="28" width="72" height="104" rx="4" fill={ILL.fill1} />
        <rect x="92" y="36" width="56" height="96" rx="3" fill={ILL.fill2} />
        <circle cx="140" cy="84" r="2.5" fill={ILL.line} />
      </g>
      <g stroke={ILL.danger} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M104 74v-8a10 10 0 0 1 20 0v8" fill="none" />
        <rect x="100" y="74" width="28" height="22" rx="4" fill={ILL.danger} />
        <path d="M114 82v6" stroke={ILL.white} strokeWidth="2" />
      </g>
      <g {...STROKE_PROPS}>
        <path d="M156 96l16 8v14h-24v-14z" fill={ILL.white} />
        <ellipse cx="160" cy="110" rx="7" ry="4.5" fill="none" />
        <circle cx="160" cy="110" r="1.5" fill={ILL.line} />
      </g>
    </svg>
  );
}

/** Карта-схема с пунктирным маршрутом в никуда, флажок (danger). */
export function Error404Illustration(props: Props): ReactNode {
  return (
    <svg {...SVG_PROPS} {...props}>
      <ellipse cx="120" cy="140" rx="88" ry="10" fill={ILL.shadow} />
      <g {...STROKE_PROPS}>
        <path d="M56 44l44-12 40 12 44-12v84l-44 12-40-12-44 12z" fill={ILL.fill1} />
        <path d="M100 32v84M140 44v84" stroke={ILL.fill2} />
        <circle cx="76" cy="100" r="5" fill={ILL.fill2} />
      </g>
      <path
        d="M76 100c16-24 36 8 52-20 6-10 14-14 22-12"
        stroke={ILL.line}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeDasharray="4 5"
        fill="none"
      />
      <g stroke={ILL.danger} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M164 40v28" />
        <path d="M164 40h20l-6 7 6 7h-20" fill={ILL.danger} />
      </g>
      <path
        d="M158 76l6 6M164 76l-6 6"
        stroke={ILL.line}
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

/** Дорожка из 3 флажков-шагов к кубку, конфетти-точки (бренд-акцент). */
export function OnboardingWelcomeIllustration(props: Props): ReactNode {
  return (
    <svg {...SVG_PROPS} {...props}>
      <ellipse cx="120" cy="140" rx="92" ry="10" fill={ILL.shadow} />
      <path
        d="M40 124c32 0 40-28 72-32s52-16 72-44"
        stroke={ILL.line}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeDasharray="5 6"
        fill="none"
      />
      <g {...STROKE_PROPS}>
        <path d="M72 116v-28" />
        <path d="M72 88h18l-5 6 5 6H72" fill={ILL.fill2} />
        <path d="M124 92v-28" />
        <path d="M124 64h18l-5 6 5 6h-18" fill={ILL.fill2} />
        <path d="M168 64v-28" />
        {/* Финальный флажок — оранжевый «огонёк» РТК (brand-rt.md §5) */}
        <path d="M168 36h18l-5 6 5 6h-18" fill={ILL.orange} />
      </g>
      <g stroke={ILL.accent} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <path
          d="M196 24h20v10a10 10 0 0 1-20 0z"
          fill={ILL.accent}
        />
        <path d="M196 27h-5a5 5 0 0 0 5 8M216 27h5a5 5 0 0 1-5 8" fill="none" />
        <path d="M206 44v6M200 52h12" />
      </g>
      <g fill={ILL.accent}>
        <circle cx="56" cy="56" r="2.5" />
        <circle cx="100" cy="40" r="2" />
        <circle cx="148" cy="96" r="2.5" />
        <circle cx="188" cy="72" r="2" />
      </g>
      <g fill={ILL.fill2}>
        <circle cx="84" cy="48" r="2.5" />
        <circle cx="136" cy="28" r="2.5" />
        <circle cx="196" cy="92" r="2.5" />
        <circle cx="48" cy="92" r="2" />
      </g>
    </svg>
  );
}

/** Колокольчик со «спящей» z-z-z пунктирной волной. */
export function NotificationsEmptyIllustration(props: Props): ReactNode {
  return (
    <svg {...SVG_PROPS} {...props}>
      <ellipse cx="120" cy="138" rx="72" ry="10" fill={ILL.shadow} />
      <g {...STROKE_PROPS}>
        <path
          d="M120 40a32 32 0 0 1 32 32v20l10 12H78l10-12V72a32 32 0 0 1 32-32z"
          fill={ILL.fill1}
        />
        <path d="M120 40v-6" />
        <circle cx="120" cy="31" r="3.5" fill={ILL.fill2} />
        <path d="M110 112a10 10 0 0 0 20 0" fill={ILL.fill2} />
        <path d="M96 68a24 24 0 0 1 12-18" stroke={ILL.fill2} />
      </g>
      <path
        d="M150 44c6-6 10-14 12-22"
        stroke={ILL.line}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeDasharray="3 5"
        fill="none"
      />
      <g stroke={ILL.accent} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" fill="none">
        <path d="M168 34h10l-10 10h10" />
        <path d="M186 18h8l-8 8h8" />
      </g>
    </svg>
  );
}
