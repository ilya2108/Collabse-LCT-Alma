import { useDraggable } from '@dnd-kit/core';
import { Plug } from 'lucide-react';
import { motion } from 'motion/react';
import { memo, type ReactNode } from 'react';
import type { RequestItem } from '@/shared/api/types';
import { motionTokens, springs } from '@/shared/lib/motion';
import { cn } from '@/shared/lib/cn';
import { Badge } from '@/shared/ui/badge';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/ui/tooltip';
import { UserAvatar } from '@/shared/ui/UserAvatar';
import { StuckBadge } from '@/components/requests/badges';
import { daysOnStage } from './boardModel';

/**
 * Карточка заявки на доске (redesign.md §6.4): полоса 3px цвета этапа слева,
 * бейджи продукта/типа/«зависла», аватар и дни на этапе. Hover — подъём
 * `-translate-y-0.5` + shadow-overlay (CSS 150ms); захват — исходник 0.35,
 * перелёт при отпускании — layoutId + springs.release, затем пульс
 * primary-tint (§2.3). dnd-kit-логика не менялась.
 */

interface BoardCardProps {
  request: RequestItem;
  draggable: boolean;
  dragDisabledHint?: string;
  productName?: string;
  onOpen: (request: RequestItem) => void;
  /** Рендер внутри DragOverlay: другой id, scale 1.02 + shadow-drag. */
  overlay?: boolean;
  /** Пульс фона после успешного дропа (§2.3). */
  pulse?: boolean;
}

export const BoardCard = memo(function BoardCard({
  request,
  draggable,
  dragDisabledHint,
  productName,
  onOpen,
  overlay = false,
  pulse = false,
}: BoardCardProps): ReactNode {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: overlay ? `overlay-${request.id}` : request.id,
    data: { request },
    disabled: !draggable || overlay,
  });

  const days = daysOnStage(request);
  const hasPendingSync = Boolean(
    request.external_refs?.lms_enrollment_id || request.external_refs?.cms_lead_id,
  );

  const card = (
    <motion.div
      layoutId={overlay ? undefined : `board-card-${request.id}`}
      layout={overlay ? undefined : 'position'}
      transition={springs.release}
      animate={overlay ? { scale: motionTokens.scale.pop } : undefined}
      // Цель тура §7.4 «board-card»: SpotlightTour берёт первый матч в DOM —
      // первую карточку первой колонки.
      data-tour={overlay ? undefined : 'board-card'}
      className={cn(
        'relative mb-2 rounded-md bg-card p-3 pl-4',
        overlay
          ? 'shadow-drag cursor-grabbing'
          : 'shadow-card transition-[transform,box-shadow] duration-150 hover:-translate-y-0.5 hover:shadow-overlay',
        !overlay && (draggable ? 'cursor-grab active:cursor-grabbing' : 'cursor-pointer'),
        isDragging && 'opacity-35',
      )}
      onClick={() => {
        if (!isDragging && !overlay) onOpen(request);
      }}
    >
      {/* полоса цвета этапа */}
      <span
        aria-hidden="true"
        className="absolute inset-y-0 left-0 w-[3px] rounded-l-md"
        style={{ backgroundColor: request.status.color || 'var(--primary)' }}
      />
      {/* пульс primary-tint после дропа (§2.3) */}
      {pulse ? (
        <motion.span
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 rounded-md bg-primary-tint"
          initial={{ opacity: 1 }}
          animate={{ opacity: 0 }}
          transition={{ duration: motionTokens.duration.normal }}
        />
      ) : null}

      <div className="mb-1.5 truncate text-sm font-medium text-foreground">{request.title}</div>

      {(productName || request.client_kind || request.is_stuck || hasPendingSync) && (
        <div className="mb-2 flex flex-wrap items-center gap-1">
          {productName ? <Badge variant="secondary">{productName}</Badge> : null}
          {request.workflow_type === 'b2c' && request.client_kind ? (
            <Badge variant="outline">
              {request.client_kind === 'person' ? 'физлицо' : 'юрлицо'}
            </Badge>
          ) : null}
          {request.is_stuck ? <StuckBadge short days={request.stuck_days ?? days} /> : null}
          {hasPendingSync ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="inline-flex" tabIndex={-1}>
                  <Plug className="size-3.5 text-status-progress-deep" aria-label="Есть связи с LMS/CMS — статус синка в карточке" />
                </span>
              </TooltipTrigger>
              <TooltipContent>Есть связи с LMS/CMS — статус синка в карточке</TooltipContent>
            </Tooltip>
          ) : null}
        </div>
      )}

      <div className="flex items-center gap-1.5">
        <UserAvatar fullName={request.assignee?.full_name} size={24} />
        <span className="text-xs text-muted-foreground">
          {days === 0 ? 'сегодня' : `${days} дн. на этапе`}
        </span>
      </div>
    </motion.div>
  );

  const wrapped = (
    <div
      ref={setNodeRef}
      {...attributes}
      {...(draggable && !overlay ? listeners : {})}
      aria-label={`Заявка «${request.title}», этап «${request.status.name}»`}
      onKeyDown={(e) => {
        if (e.key === 'Enter' && !overlay) onOpen(request);
      }}
      style={{ touchAction: 'none' }}
      className="rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
    >
      {card}
    </div>
  );

  if (!draggable && dragDisabledHint && !overlay) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>{wrapped}</TooltipTrigger>
        <TooltipContent>{dragDisabledHint}</TooltipContent>
      </Tooltip>
    );
  }
  return wrapped;
});
