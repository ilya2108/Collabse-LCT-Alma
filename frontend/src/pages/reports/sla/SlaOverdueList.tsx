import { BellRing, CircleSlash, Repeat2 } from 'lucide-react';
import type { ReactNode } from 'react';
import type { SlaOverdueItem } from '@/shared/api/endpoints/sla';
import { StageTag, StatusBadge } from '@/shared/ui/data-table';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/ui/tooltip';
import { RequestLink } from '../KamActivityParts';
import {
  bucketLabel,
  bucketTone,
  counterpartyName,
  formatDays,
  formatDelay,
  lastActionText,
  mskDay,
  mskShortDay,
  normSourceLabel,
  shortName,
} from './slaFormat';

/**
 * Просроченные заявки по убыванию задержки: заявка (клик — карточка поверх отчёта),
 * вуз / клиент, этап на дату среза, на этапе / норма с источником, сверх нормы (цвет
 * корзины), просрочена с, последнее действие, напоминания. Бейджи «без действий после
 * срока» и «получена просроченной DD.MM» — факты. ≥ md — таблица, ниже — карточки.
 */

function Flags({ item }: { item: SlaOverdueItem }): ReactNode {
  if (!item.no_action_after_due && !item.inherited) return null;
  return (
    <span className="flex flex-wrap gap-1">
      {item.no_action_after_due ? (
        <StatusBadge status="danger" size="sm" icon={CircleSlash}>
          без действий после срока
        </StatusBadge>
      ) : null}
      {item.inherited ? (
        <StatusBadge status="draft" size="sm" icon={Repeat2}>
          получена просроченной{item.with_kam_since ? ` ${mskShortDay(item.with_kam_since)}` : ''}
        </StatusBadge>
      ) : null}
    </span>
  );
}

function DelayBadge({ item }: { item: SlaOverdueItem }): ReactNode {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span tabIndex={0} className="inline-flex rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring">
          <StatusBadge status={bucketTone(item.bucket)} size="sm" className="tabular">
            {formatDelay(item.delay_days)}
          </StatusBadge>
        </span>
      </TooltipTrigger>
      <TooltipContent>Сверх нормы: корзина «{bucketLabel(item.bucket)}»</TooltipContent>
    </Tooltip>
  );
}

function Reminders({ item }: { item: SlaOverdueItem }): ReactNode {
  if (item.reminders === 0 && !item.escalated) {
    return <span className="text-muted-foreground">—</span>;
  }
  return (
    <span className="flex flex-col items-end gap-0.5">
      <span className="tabular inline-flex items-center gap-1">
        <BellRing className="size-3 text-muted-foreground" aria-hidden="true" />
        {item.reminders}
        <span className="text-xs text-muted-foreground">(доставлено {item.delivered})</span>
      </span>
      {item.escalated ? (
        <span className="text-xs text-status-warning-deep">эскалирована руководителю</span>
      ) : null}
    </span>
  );
}

function OnStage({ item }: { item: SlaOverdueItem }): ReactNode {
  return (
    <span className="flex flex-col leading-tight">
      <span className="tabular whitespace-nowrap">
        {formatDays(item.on_stage_days)} / {formatDays(item.norm_days)}
      </span>
      <span className="text-xs text-muted-foreground">норма {normSourceLabel(item.norm_source)}</span>
    </span>
  );
}

interface SlaOverdueListProps {
  items: SlaOverdueItem[];
  onOpenRequest: (id: string) => void;
  /** Колонка «КАМ» — для общего списка; в разборе КАМа не нужна. */
  showKam?: boolean;
  /** < md — карточки. */
  compact: boolean;
  /** Всего просроченных и признак обрезки списка (полный — в XLSX). */
  total?: number;
  truncated?: boolean;
  emptyText?: string;
}

export function SlaOverdueList({
  items,
  onOpenRequest,
  showKam = false,
  compact,
  total,
  truncated = false,
  emptyText = 'Просроченных заявок нет',
}: SlaOverdueListProps): ReactNode {
  if (items.length === 0) {
    return <p className="px-2 py-3 text-sm text-muted-foreground">{emptyText}</p>;
  }
  const footer =
    truncated || (total !== undefined && total > items.length) ? (
      <p className="px-2 pt-2 text-xs text-muted-foreground">
        Показано {items.length} из {total ?? items.length}; полный список — в выгрузке XLSX.
      </p>
    ) : null;

  if (compact) {
    return (
      <div className="flex flex-col gap-2">
        <ul className="flex flex-col gap-2">
          {items.map((item) => (
            <li
              key={item.request_id}
              className="flex min-w-0 flex-col gap-1.5 rounded-md border border-border bg-card p-3"
            >
              <div className="flex items-start justify-between gap-2">
                <RequestLink request={item} onOpen={onOpenRequest} className="min-w-0 text-sm" />
                <DelayBadge item={item} />
              </div>
              <span className="text-xs text-muted-foreground">
                {counterpartyName(item.university, item.client)}
                {showKam ? ` · ${shortName(item.kam)}` : ''}
              </span>
              <span className="flex flex-wrap items-center gap-1.5 text-xs">
                <StageTag size="sm" color={item.stage.color} name={item.stage.name} />
                <span className="tabular text-muted-foreground">
                  {formatDays(item.on_stage_days)} / норма {formatDays(item.norm_days)} · просрочена с{' '}
                  {mskShortDay(item.overdue_since)}
                </span>
              </span>
              <span className="text-xs text-muted-foreground">
                Последнее действие: {lastActionText(item.last_action)}
                {item.reminders > 0
                  ? ` · напоминаний ${item.reminders} (доставлено ${item.delivered})`
                  : ''}
                {item.escalated ? ' · эскалирована' : ''}
              </span>
              <Flags item={item} />
            </li>
          ))}
        </ul>
        {footer}
      </div>
    );
  }

  return (
    <div className="flex min-w-0 flex-col">
      <div className="max-w-full overflow-x-auto">
        <Table className={showKam ? 'min-w-[1040px]' : 'min-w-[920px]'}>
          <TableHeader>
            <TableRow>
              <TableHead>Заявка</TableHead>
              {showKam ? <TableHead>КАМ</TableHead> : null}
              <TableHead>Этап</TableHead>
              <TableHead>На этапе / норма</TableHead>
              <TableHead className="text-right">Сверх нормы</TableHead>
              <TableHead>Просрочена с</TableHead>
              <TableHead>Последнее действие</TableHead>
              <TableHead className="text-right">Напоминаний</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => (
              <TableRow key={item.request_id}>
                <TableCell className="max-w-[280px] align-top">
                  <span className="flex flex-col gap-1">
                    <RequestLink request={item} onOpen={onOpenRequest} className="text-sm" />
                    <span className="text-xs text-muted-foreground">
                      {counterpartyName(item.university, item.client)}
                    </span>
                    <Flags item={item} />
                  </span>
                </TableCell>
                {showKam ? <TableCell className="align-top">{shortName(item.kam)}</TableCell> : null}
                <TableCell className="align-top">
                  <span className="flex flex-col gap-1">
                    <StageTag size="sm" color={item.stage.color} name={item.stage.name} />
                    <span className="tabular text-xs text-muted-foreground">
                      с {mskDay(item.stage_since)}
                    </span>
                  </span>
                </TableCell>
                <TableCell className="align-top">
                  <OnStage item={item} />
                </TableCell>
                <TableCell className="text-right align-top">
                  <DelayBadge item={item} />
                </TableCell>
                <TableCell className="tabular align-top">{mskDay(item.overdue_since)}</TableCell>
                <TableCell className="max-w-[220px] align-top text-sm">
                  {item.last_action ? (
                    <span className="flex flex-col leading-tight">
                      <span>{lastActionText(item.last_action)}</span>
                      <span className="tabular text-xs text-muted-foreground">
                        {mskDay(item.last_action.at)}
                      </span>
                    </span>
                  ) : (
                    <span className="text-muted-foreground">{lastActionText(null)}</span>
                  )}
                </TableCell>
                <TableCell className="text-right align-top">
                  <Reminders item={item} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      {footer}
    </div>
  );
}
