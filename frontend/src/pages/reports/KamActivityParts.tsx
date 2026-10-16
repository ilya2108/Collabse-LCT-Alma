import { CalendarDays, CornerUpLeft, FileText, Flame, MessageSquare, Shuffle } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { MouseEvent, ReactNode } from 'react';
import type {
  KamActivityInteractions,
  KamActivityRequest,
  KamActivityStageChip,
} from '@/shared/api/endpoints/kamActivity';
import { cn } from '@/shared/lib/cn';
import { dayjs } from '@/shared/lib/dayjs';
import { StageTag, StatusBadge } from '@/shared/ui/data-table';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/ui/tooltip';
import {
  daysAgo,
  formatDay,
  formatDays,
  formatShortDay,
  interactionsShort,
} from './kamActivityModel';

/**
 * Общие ячейки дерева и мобильных карточек отчёта «Активность КАМов»:
 * взаимодействия П/К/Ф/М с легендой, последний контакт с предупреждением,
 * чипы этапов, ссылка на заявку (клик — карточка поверх отчёта, с модификатором —
 * новая вкладка), дни на этапе с «огоньком» зависшей.
 */

const PART_ICONS: Array<{
  key: keyof Omit<KamActivityInteractions, 'total' | 'returns'>;
  icon: LucideIcon;
  label: string;
}> = [
  { key: 'transitions', icon: Shuffle, label: 'Переходы по этапам' },
  { key: 'comments', icon: MessageSquare, label: 'Комментарии' },
  { key: 'files', icon: FileText, label: 'Файлы' },
  { key: 'activities', icon: CalendarDays, label: 'Мероприятия с вузом' },
];

export function InteractionsCell({
  interactions,
  compact = false,
}: {
  interactions: KamActivityInteractions;
  compact?: boolean;
}): ReactNode {
  const zero = interactions.total === 0;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          tabIndex={0}
          className={cn(
            'inline-flex flex-wrap items-center gap-x-2 gap-y-0.5 rounded-sm text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring',
            zero && 'text-muted-foreground',
          )}
          aria-label={`Взаимодействий ${interactions.total}: ${PART_ICONS.map(
            (part) => `${part.label.toLowerCase()} ${interactions[part.key]}`,
          ).join(', ')}${interactions.returns ? `, возвратов ${interactions.returns}` : ''}`}
        >
          <span className="tabular font-semibold">{interactions.total}</span>
          {compact
            ? null
            : PART_ICONS.map(({ key, icon: Icon }) => (
                <span
                  key={key}
                  className={cn(
                    'tabular inline-flex items-center gap-0.5 text-xs',
                    interactions[key] === 0 ? 'text-muted-foreground/60' : 'text-muted-foreground',
                  )}
                >
                  <Icon className="size-3" aria-hidden="true" />
                  {interactions[key]}
                </span>
              ))}
          {interactions.returns > 0 ? (
            <span className="tabular inline-flex items-center gap-0.5 text-xs text-status-warning-deep">
              <CornerUpLeft className="size-3" aria-hidden="true" />
              {interactions.returns}
            </span>
          ) : null}
        </span>
      </TooltipTrigger>
      <TooltipContent>
        <div className="flex flex-col gap-0.5">
          {PART_ICONS.map((part) => (
            <span key={part.key}>
              {part.label}: {interactions[part.key]}
            </span>
          ))}
          {interactions.returns > 0 ? <span>Из них возвратов: {interactions.returns}</span> : null}
          <span className="opacity-80">Только собственные действия ответственного КАМа</span>
        </div>
      </TooltipContent>
    </Tooltip>
  );
}

/** Неинтерактивная строка «N · П a · К b · Ф c · М d» — для заголовков-кнопок карточек. */
export function InteractionsText({
  interactions,
}: {
  interactions: KamActivityInteractions;
}): ReactNode {
  return (
    <span className="tabular text-xs text-muted-foreground">
      <span className="font-semibold text-foreground">{interactions.total}</span> ·{' '}
      {interactionsShort(interactions)}
      {interactions.returns > 0 ? ` · ↩ ${interactions.returns}` : ''}
    </span>
  );
}

/** Последний контакт: дата и «N дн. назад»; раньше начала периода или нет — warning. */
export function LastContact({
  value,
  asOf,
  periodFrom,
}: {
  value: string | null;
  asOf: string;
  periodFrom: string;
}): ReactNode {
  const stale = !value || dayjs(value).isBefore(dayjs(`${periodFrom}T00:00:00+03:00`));
  return (
    <span className={cn('flex flex-col leading-tight', stale && 'text-status-warning-deep')}>
      <span className="tabular text-sm">{value ? formatDay(value) : '—'}</span>
      <span className={cn('text-xs', stale ? 'text-status-warning-deep' : 'text-muted-foreground')}>
        {daysAgo(value, asOf)}
      </span>
    </span>
  );
}

export function StageChips({
  stages,
  limit,
}: {
  stages: KamActivityStageChip[];
  limit?: number;
}): ReactNode {
  if (stages.length === 0) return <span className="text-muted-foreground">—</span>;
  const shown = limit ? stages.slice(0, limit) : stages;
  const rest = stages.length - shown.length;
  return (
    <span className="flex flex-wrap items-center gap-1">
      {shown.map((stage) => (
        <StageTag
          key={stage.id}
          size="sm"
          color={stage.color}
          name={`${stage.name}${stage.count > 1 ? ` ×${stage.count}` : ''}`}
        />
      ))}
      {rest > 0 ? <span className="text-xs text-muted-foreground">+{rest}</span> : null}
    </span>
  );
}

export function OutcomeBadge({ outcome }: { outcome: 'won' | 'lost' | null }): ReactNode {
  if (outcome === 'won') {
    return (
      <StatusBadge status="success" size="sm">
        Выиграна
      </StatusBadge>
    );
  }
  if (outcome === 'lost') {
    return (
      <StatusBadge status="danger" size="sm">
        Отказ
      </StatusBadge>
    );
  }
  return null;
}

export function DaysOnStage({ request }: { request: KamActivityRequest }): ReactNode {
  if (request.stage.is_terminal) {
    return <span className="text-muted-foreground">—</span>;
  }
  return (
    <span
      className={cn(
        'tabular inline-flex items-center gap-1 text-sm',
        request.is_stuck && 'font-medium text-status-warning-deep',
      )}
    >
      {formatDays(request.days_on_stage)}
      {request.is_stuck ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <span tabIndex={0} className="inline-flex rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring">
              <Flame className="size-3.5" aria-label={`Зависла: порог ${request.threshold_days} дн.`} />
            </span>
          </TooltipTrigger>
          <TooltipContent>Зависла: порог {request.threshold_days} дн.</TooltipContent>
        </Tooltip>
      ) : null}
    </span>
  );
}

export function RequestStageCell({ request }: { request: KamActivityRequest }): ReactNode {
  return (
    <span className="flex flex-wrap items-center gap-1.5">
      <StageTag size="sm" color={request.stage.color} name={request.stage.name} />
      <span className="tabular text-xs text-muted-foreground">с {formatShortDay(request.stage_since)}</span>
    </span>
  );
}

/**
 * Ссылка на заявку: обычный клик открывает карточку поверх отчёта (URL `request=`),
 * клик с модификатором или средней кнопкой — штатное поведение ссылки (новая вкладка).
 */
export function RequestLink({
  request,
  onOpen,
  className,
}: {
  request: Pick<KamActivityRequest, 'request_id' | 'title'>;
  onOpen: (id: string) => void;
  className?: string;
}): ReactNode {
  const onClick = (event: MouseEvent<HTMLAnchorElement>): void => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) {
      return;
    }
    event.preventDefault();
    onOpen(request.request_id);
  };
  return (
    <a
      href={`/requests/${request.request_id}`}
      onClick={onClick}
      className={cn(
        'rounded-sm font-medium text-foreground underline-offset-2 outline-none hover:text-primary hover:underline focus-visible:ring-2 focus-visible:ring-ring',
        className,
      )}
    >
      {request.title}
    </a>
  );
}

export function initials(fullName: string): string {
  const parts = fullName.split(/\s+/).filter(Boolean);
  return parts
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? '')
    .join('');
}

export function KamAvatar({ name }: { name: string }): ReactNode {
  return (
    <span
      aria-hidden="true"
      className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary-tint-2 text-[11px] font-semibold text-primary-active"
    >
      {initials(name)}
    </span>
  );
}

export function KamBadges({ isActive, roles }: { isActive: boolean; roles: string[] }): ReactNode {
  return (
    <>
      {!isActive ? (
        <StatusBadge status="draft" size="sm">
          Неактивен
        </StatusBadge>
      ) : null}
      {roles.includes('head_kam') ? (
        <StatusBadge status="talent" size="sm">
          Руководитель
        </StatusBadge>
      ) : null}
    </>
  );
}
