import { useDroppable } from '@dnd-kit/core';
import { motion } from 'motion/react';
import type { ReactNode } from 'react';
import type { RequestItem } from '@/shared/api/types';
import { stageText, stageTint } from '@/shared/config/tokens';
import { formatMoney } from '@/shared/lib/format';
import { springs } from '@/shared/lib/motion';
import { cn } from '@/shared/lib/cn';
import { BoardCard } from './BoardCard';
import type { BoardColumnModel } from './boardModel';

/**
 * Колонка канбана (redesign.md §6.4): фон bg-muted/60, заголовок — точка цвета
 * этапа + имя + счётчик (тинт цвета этапа) + сумма tabular. При drag
 * разрешённые колонки подсвечиваются ring-primary/60 (§2.3), запрещённые
 * затемняются. Терминальные этапы — «стопки» 48px, разворачиваются
 * springs.gentle. Логика droppable не менялась.
 */

export type ColumnDropState = 'idle' | 'allowed' | 'forbidden' | 'source';

interface BoardColumnProps {
  column: BoardColumnModel;
  dropState: ColumnDropState;
  collapsed: boolean;
  onToggleCollapsed: (statusId: string) => void;
  canDrag: (request: RequestItem) => boolean;
  dragHint: (request: RequestItem) => string | undefined;
  productName: (request: RequestItem) => string | undefined;
  onOpen: (request: RequestItem) => void;
  /** Заявка, получившая пульс после дропа (§2.3). */
  pulseRequestId?: string | null;
  /** Порядковый номер для stagger-входа колонок (§2.3, максимум 12). */
  index?: number;
  emptyContent?: ReactNode;
}

export function BoardColumn({
  column,
  dropState,
  collapsed,
  onToggleCollapsed,
  canDrag,
  dragHint,
  productName,
  onOpen,
  pulseRequestId,
  index = 0,
  emptyContent,
}: BoardColumnProps): ReactNode {
  const { status, requests, totalAmount } = column;
  // Запрещённые колонки остаются droppable: дроп в них отбивается тостом
  // с перечнем разрешённых этапов (ux.md §7.2), а не «молчаливым» отскоком.
  const { setNodeRef, isOver } = useDroppable({
    id: status.id,
    data: { statusId: status.id },
    disabled: collapsed || dropState === 'source',
  });

  if (collapsed) {
    return (
      <button
        type="button"
        onClick={() => onToggleCollapsed(status.id)}
        aria-label={`Развернуть колонку «${status.name}» (${requests.length})`}
        aria-expanded={false}
        className={cn(
          'flex w-12 min-w-12 shrink-0 flex-col items-center gap-3 self-stretch rounded-lg border bg-card py-3',
          'transition-[background-color,border-color] duration-150 hover:bg-primary-tint',
        )}
      >
        <span
          className="tabular inline-flex min-w-5 items-center justify-center rounded-sm px-1 py-0.5 text-xs font-medium"
          style={{ backgroundColor: stageTint(status.color), color: stageText(status.color) }}
        >
          {requests.length}
        </span>
        <span
          className="rotate-180 text-xs text-muted-foreground"
          style={{ writingMode: 'vertical-rl' }}
        >
          {status.name}
        </span>
      </button>
    );
  }

  return (
    <motion.section
      ref={setNodeRef}
      aria-label={`Этап «${status.name}», заявок: ${requests.length}`}
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ ...springs.gentle, delay: Math.min(index, 12) * 0.06 }}
      className={cn(
        'flex max-h-full w-[300px] min-w-[300px] snap-start flex-col rounded-lg bg-muted/60',
        'transition-[box-shadow,background-color,opacity] duration-150',
        dropState === 'allowed' && 'ring-2 ring-primary/60',
        dropState === 'forbidden' && 'opacity-50!',
        dropState === 'source' && 'outline outline-1 outline-dashed outline-border',
        isOver && dropState === 'allowed' && 'bg-primary-tint',
      )}
    >
      <div className="flex items-center gap-2 border-b border-border/60 px-3 py-2.5">
        <span
          aria-hidden="true"
          className="size-2 shrink-0 rounded-full"
          style={{ backgroundColor: status.color }}
        />
        {status.is_terminal ? (
          <button
            type="button"
            onClick={() => onToggleCollapsed(status.id)}
            aria-expanded
            className="min-w-0 flex-1 truncate text-left text-sm font-medium hover:text-primary"
          >
            {status.name}
          </button>
        ) : (
          <span className="min-w-0 flex-1 truncate text-sm font-medium">{status.name}</span>
        )}
        <span
          className="tabular inline-flex min-w-5 items-center justify-center rounded-sm px-1.5 py-0.5 text-xs font-medium"
          style={{ backgroundColor: stageTint(status.color), color: stageText(status.color) }}
        >
          {requests.length}
        </span>
      </div>
      {totalAmount > 0 ? (
        <div className="tabular px-3 pt-1.5 text-xs text-muted-foreground">
          {formatMoney(totalAmount)}
        </div>
      ) : null}
      <div className="min-h-16 flex-1 overflow-y-auto p-3">
        {requests.map((request) => (
          <BoardCard
            key={request.id}
            request={request}
            draggable={canDrag(request)}
            dragDisabledHint={dragHint(request)}
            productName={productName(request)}
            pulse={pulseRequestId === request.id}
            onOpen={onOpen}
          />
        ))}
        {requests.length === 0 ? emptyContent : null}
      </div>
    </motion.section>
  );
}
