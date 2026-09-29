import { motion } from 'motion/react';
import type { ReactNode } from 'react';
import { springs } from '@/shared/lib/motion';

/**
 * Пустой канбан (redesign.md §3.5 + §4.2 «board-empty»): доска с пунктирной
 * карточкой-плюсом и рукой-курсором, ≤ 1 предложение, CTA. Иллюстрация —
 * инлайн-SVG в дуотоне §4.1 (линии #0F2547, плоскости tint, акцент primary).
 */

function BoardEmptyIllustration(): ReactNode {
  return (
    <svg viewBox="0 0 240 160" width={200} height={133} aria-hidden="true" fill="none">
      <ellipse cx="120" cy="146" rx="84" ry="8" fill="var(--muted, #EEF2F8)" />
      <g stroke="var(--sidebar, #0F2547)" strokeWidth="1.5" strokeLinejoin="round">
        {/* доска: 3 колонки */}
        <rect x="36" y="20" width="52" height="104" rx="8" fill="var(--primary-tint, #EEF3FB)" />
        <rect x="94" y="20" width="52" height="104" rx="8" fill="var(--primary-tint, #EEF3FB)" />
        <rect x="152" y="20" width="52" height="104" rx="8" fill="var(--primary-tint, #EEF3FB)" />
        {/* карточки первой колонки */}
        <rect x="44" y="32" width="36" height="20" rx="4" fill="#fff" />
        <rect x="44" y="58" width="36" height="20" rx="4" fill="#fff" />
        {/* карточка второй колонки + акцентная, которую «тянут» */}
        <rect x="102" y="32" width="36" height="20" rx="4" fill="#fff" />
        <rect x="130" y="76" width="40" height="24" rx="4" fill="var(--primary-tint-2, #DCE7F5)" stroke="var(--primary, #1E56A0)" />
      </g>
      {/* пунктирная карточка с плюсом в третьей колонке */}
      <rect
        x="160"
        y="32"
        width="36"
        height="20"
        rx="4"
        stroke="var(--primary, #1E56A0)"
        strokeWidth="1.5"
        strokeDasharray="4 3"
        fill="none"
      />
      <path d="M178 37v10M173 42h10" stroke="var(--primary, #1E56A0)" strokeWidth="1.5" strokeLinecap="round" />
      {/* рука-курсор тянет карточку */}
      <g stroke="var(--sidebar, #0F2547)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M158 104v-8a3 3 0 0 1 6 0v6m0-4a3 3 0 0 1 6 0v4m0-2a3 3 0 0 1 6 0v3m0-1a3 3 0 0 1 6 0v7c0 8-4 13-12 13s-12-5-12-13v-5" fill="#fff" />
      </g>
      {/* пунктирная траектория переноса */}
      <path
        d="M118 44c18 6 30 16 34 30"
        stroke="var(--primary, #1E56A0)"
        strokeWidth="1.5"
        strokeDasharray="3 4"
        strokeLinecap="round"
      />
    </svg>
  );
}

interface BoardEmptyStateProps {
  title: string;
  description?: string;
  actions?: ReactNode;
}

export function BoardEmptyState({ title, description, actions }: BoardEmptyStateProps): ReactNode {
  return (
    <div className="flex flex-col items-center gap-3 py-8 text-center">
      <motion.div
        initial={{ opacity: 0, y: 12, scale: 0.96 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={springs.bouncy}
      >
        <BoardEmptyIllustration />
      </motion.div>
      <div>
        <div className="text-base font-semibold text-balance">{title}</div>
        {description ? <div className="mt-1 text-sm text-muted-foreground">{description}</div> : null}
      </div>
      {actions ? <div className="flex flex-col items-center gap-2">{actions}</div> : null}
    </div>
  );
}
