import { AnimatePresence, motion } from 'motion/react';
import type { LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import type { SemanticStatus } from '@/shared/config/tokens';
import { springs } from '@/shared/lib/motion';
import { cn } from '@/shared/lib/cn';

/**
 * Timeline (redesign.md §3.7): вертикальная лента событий с иконками по типам —
 * событие считывается без чтения текста. Узел — иконка в круге bg-{tone}-tint /
 * text-{tone}-deep, возвраты — danger. Вход новых элементов (SSE) —
 * AnimatePresence mode="popLayout", fade+y springs.gentle.
 * Контракт заморожен (§9) — менять только правкой redesign.md.
 */

export interface TimelineItem {
  id: string;
  /** Тип события: ArrowRightLeft, Paperclip, MessageSquare, Plug, Bell… */
  icon: LucideIcon;
  /** Возврат = 'danger' (красный «↩»). */
  tone: SemanticStatus;
  /** «Иванов перевёл: Переговоры → Согласование». */
  title: ReactNode;
  /** Относительное до 24ч, далее дата (ux.md §4.4). */
  time: string;
  /** Комментарий причины, ссылка «показать JSON». */
  content?: ReactNode;
}

export interface TimelineProps {
  items: TimelineItem[];
  /** Чипы-фильтры над лентой. */
  filter?: ReactNode;
}

/** Статичные классы по тону — tailwind не собирает динамические имена. */
const TONE_CLASSES: Record<SemanticStatus, string> = {
  draft: 'bg-status-draft-tint text-status-draft-deep',
  progress: 'bg-status-progress-tint text-status-progress-deep',
  success: 'bg-status-success-tint text-status-success-deep',
  warning: 'bg-status-warning-tint text-status-warning-deep',
  danger: 'bg-status-danger-tint text-status-danger-deep',
  talent: 'bg-status-talent-tint text-status-talent-deep',
};

export function Timeline({ items, filter }: TimelineProps): ReactNode {
  return (
    <div>
      {filter ? <div className="mb-4 flex flex-wrap gap-1.5">{filter}</div> : null}
      <ol className="relative m-0 list-none p-0">
        {/* вертикальная линия под узлами */}
        <span aria-hidden="true" className="absolute bottom-3 left-3.5 top-3 w-px bg-border" />
        <AnimatePresence mode="popLayout" initial={false}>
          {items.map((item) => {
            const Icon = item.icon;
            return (
              <motion.li
                key={item.id}
                layout="position"
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={springs.gentle}
                className="relative flex gap-3 pb-5 last:pb-0"
              >
                <span
                  aria-hidden="true"
                  className={cn(
                    'relative z-[1] mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full ring-4 ring-card',
                    TONE_CLASSES[item.tone],
                  )}
                >
                  <Icon className="size-3.5" aria-hidden="true" />
                </span>
                <div className="min-w-0 flex-1 pt-1">
                  <div className="text-sm text-foreground">{item.title}</div>
                  {item.content ? <div className="mt-1 text-sm">{item.content}</div> : null}
                  <div className="mt-0.5 text-xs text-muted-foreground">{item.time}</div>
                </div>
              </motion.li>
            );
          })}
        </AnimatePresence>
      </ol>
    </div>
  );
}
