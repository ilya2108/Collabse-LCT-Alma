import { ChevronRight } from 'lucide-react';
import { motion } from 'motion/react';
import { Fragment, type ReactNode } from 'react';
import type { StudentFunnelStatus } from '@/shared/api/types';
import { cn } from '@/shared/lib/cn';
import { springs } from '@/shared/lib/motion';
import { useCountUp } from '@/shared/lib/useCountUp';
import { FUNNEL_META, FUNNEL_ORDER } from './model';

/**
 * Воронка-переключатель (redesign.md §6.7): 4 крупных сегмента-карточки
 * со счётчиком 28px (count-up) и подписью; активный — заливка talent-tint
 * + ring talent (скользящая подложка, springs.snappy); сегменты соединены
 * стрелками; клик фильтрует реестр/доску.
 */

interface FunnelSwitcherProps {
  active: StudentFunnelStatus | null;
  counts: Partial<Record<StudentFunnelStatus, number>>;
  onSelect: (status: StudentFunnelStatus | null) => void;
}

function SegmentCount({ value }: { value: number | undefined }): ReactNode {
  const animated = useCountUp(value ?? 0);
  return (
    <span className="text-[28px] font-semibold leading-9 tabular">
      {value === undefined ? '…' : animated}
    </span>
  );
}

export function FunnelSwitcher({ active, counts, onSelect }: FunnelSwitcherProps): ReactNode {
  return (
    <div
      className="flex items-stretch gap-1 overflow-x-auto rounded-lg border bg-card p-2 shadow-card"
      data-tour="talent-funnel"
      role="group"
      aria-label="Фильтр по статусу воронки"
    >
      {FUNNEL_ORDER.map((status, index) => {
        const meta = FUNNEL_META[status];
        const selected = active === status;
        return (
          <Fragment key={status}>
            {index > 0 ? (
              <ChevronRight className="size-4 shrink-0 self-center text-border" aria-hidden="true" />
            ) : null}
            <button
              type="button"
              aria-pressed={selected}
              onClick={() => onSelect(selected ? null : status)}
              className={cn(
                'relative min-w-28 flex-1 rounded-md px-4 py-2 text-left transition-[color,background-color,border-color,box-shadow,transform] duration-150',
                selected ? 'text-status-talent-deep' : 'hover:bg-accent',
              )}
            >
              {selected ? (
                <motion.span
                  layoutId="talent-funnel-thumb"
                  transition={springs.snappy}
                  className="absolute inset-0 rounded-md bg-status-talent-tint ring-2 ring-status-talent"
                  aria-hidden="true"
                />
              ) : null}
              <span className="relative flex flex-col">
                <SegmentCount value={counts[status]} />
                <span className="flex items-center gap-1.5 text-xs font-medium">
                  <span
                    className="size-2 rounded-full ring-1 ring-border"
                    style={{ backgroundColor: meta.color }}
                    aria-hidden="true"
                  />
                  {meta.label}
                </span>
              </span>
            </button>
          </Fragment>
        );
      })}
    </div>
  );
}
