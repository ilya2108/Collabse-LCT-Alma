import type { ReactNode } from 'react';

/**
 * Мини-SVG-схема эскалации (redesign.md §6.8): «КАМ → N дн. → Руководитель»
 * в стиле иллюстраций §4 (дуотон, контур сайдбара, один warning-акцент) —
 * вместо текстового описания. Дублируется скрытым текстом для скринридеров.
 */

interface EscalationDiagramProps {
  /** Порог «зависания» в днях; null — показать «N». */
  days: number | null;
}

const LINE = 'var(--sidebar, #0F2547)';
const FILL_1 = 'var(--primary-tint, #EEF3FB)';
const FILL_2 = 'var(--primary-tint-2, #DCE7F5)';
const WARN = 'var(--status-warning, #D97E00)';
const WARN_DEEP = 'var(--status-warning-deep, #9A5B00)';
const WARN_TINT = 'var(--status-warning-tint, #FCF0E0)';
const TEXT = 'var(--foreground, #1F2733)';
const MUTED = 'var(--muted-foreground, #5A6B80)';

export function EscalationDiagram({ days }: EscalationDiagramProps): ReactNode {
  const n = days === null ? 'N' : String(days);
  const n15 = days === null ? '1.5×N' : String(Math.ceil(days * 1.5));
  const description =
    `Заявка без смены этапа ${n} дней — уведомление ответственному КАМу; ` +
    `${n15} дней — эскалация руководителю КАМа.`;
  return (
    <figure className="m-0">
      <svg
        viewBox="0 0 558 96"
        width="100%"
        style={{ maxWidth: 558, height: 'auto', display: 'block' }}
        role="img"
        aria-label={description}
        xmlns="http://www.w3.org/2000/svg"
        fontFamily="'Inter Variable', 'Segoe UI', system-ui, sans-serif"
      >
        <g stroke={LINE} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          {/* Заявка у КАМа */}
          <rect x="8" y="28" width="112" height="40" rx="8" fill={FILL_1} />
          {/* стрелка 1 */}
          <path d="M124 48h48M168 44l6 4-6 4" fill="none" />
          {/* Порог: уведомление КАМу (широкий узел — текст «14 дн. — …» помещается) */}
          <rect x="178" y="28" width="190" height="40" rx="8" fill={FILL_2} />
          {/* стрелка 2 (пунктир — эскалация) */}
          <path d="M372 48h48M416 44l6 4-6 4" fill="none" strokeDasharray="4 4" />
          {/* Руководитель */}
          <rect x="426" y="28" width="124" height="40" rx="8" fill={FILL_1} />
        </g>
        {/* огонёк порога — левее текстового блока, с зазором от него */}
        <path
          d="M196 40c3 2 5 5 5 8a5 5 0 0 1-10 0c0-2 1-3 2-5 1 2 3 2 3-3z"
          fill={WARN}
          stroke={WARN_DEEP}
          strokeWidth="1"
        />
        <text x="64" y="46" textAnchor="middle" fontSize="12" fontWeight="600" fill={TEXT}>
          КАМ
        </text>
        <text x="64" y="60" textAnchor="middle" fontSize="10" fill={MUTED}>
          заявка без движения
        </text>
        {/* текст среднего узла — от левого края (start), чтобы не наехать на огонёк */}
        <text x="210" y="46" textAnchor="start" fontSize="12" fontWeight="600" fill={WARN_DEEP}>
          {n} дн. — уведомление
        </text>
        <text x="210" y="60" textAnchor="start" fontSize="10" fill={MUTED}>
          ответственному КАМу
        </text>
        <text x="488" y="46" textAnchor="middle" fontSize="12" fontWeight="600" fill={TEXT}>
          Руководитель
        </text>
        <text x="488" y="60" textAnchor="middle" fontSize="10" fill={MUTED}>
          эскалация при {n15} дн.
        </text>
        {/* подписи-плашки над стрелками */}
        <rect x="119" y="8" width="60" height="16" rx="4" fill={WARN_TINT} />
        <text x="149" y="19.5" textAnchor="middle" fontSize="10" fontWeight="500" fill={WARN_DEEP}>
          порог
        </text>
        <rect x="361" y="8" width="72" height="16" rx="4" fill={WARN_TINT} />
        <text x="397" y="19.5" textAnchor="middle" fontSize="10" fontWeight="500" fill={WARN_DEEP}>
          1.5× порога
        </text>
      </svg>
      <figcaption className="sr-only">{description}</figcaption>
    </figure>
  );
}
