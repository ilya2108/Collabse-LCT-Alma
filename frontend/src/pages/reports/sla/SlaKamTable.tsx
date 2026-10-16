import { ArrowDown, ArrowUp, ArrowUpDown, ChevronDown, ChevronRight, MoveDown, MoveUp } from 'lucide-react';
import { Fragment, useMemo, useState, type ReactNode } from 'react';
import type { SlaBucketKey, SlaInactiveOwners, SlaKamRow, SlaOverdueItem } from '@/shared/api/endpoints/sla';
import { cn } from '@/shared/lib/cn';
import { Button } from '@/shared/ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/ui/tooltip';
import { KamAvatar, KamBadges } from '../KamActivityParts';
import { BUCKET_COLORS } from './slaCharts';
import { SlaOverdueList } from './SlaOverdueList';
import {
  BUCKET_KEYS,
  EMPTY,
  bucketLabel,
  compareKamRows,
  deltaCaption,
  deltaTone,
  formatInt,
  formatNumber1,
  formatPct,
  loadMark,
  ofText,
  samplePct,
  type KamSortKey,
  type SortDir,
} from './slaFormat';

/**
 * Таблица «КАМы» вкладки «Просрочки»: в работе (относительно медианы команды),
 * просрочено с мини-полосой старения, получено при передаче, без действий после срока,
 * сверх нормы (среднее · медиана · максимум с n), «Уложились в норму», Δ просроченных
 * к срезу на начало периода. По умолчанию — собственные просрочки ↓, затем доля ↓.
 * Раскрытие строки — просроченные заявки КАМа и «Подробный разбор». < md — карточки.
 */

interface SlaKamTableProps {
  kams: SlaKamRow[];
  overdueItems: SlaOverdueItem[];
  activeMedian: number | null;
  inactiveOwners: SlaInactiveOwners | null;
  snapshotLabel: string;
  compact: boolean;
  onSelectKam: (kamId: string) => void;
  onOpenRequest: (id: string) => void;
}

const COLUMNS: Array<{ key: KamSortKey; label: string; align?: 'right' }> = [
  { key: 'kam', label: 'КАМ' },
  { key: 'active', label: 'В работе', align: 'right' },
  { key: 'overdue', label: 'Просрочено', align: 'right' },
  { key: 'inherited', label: 'Получено при передаче', align: 'right' },
  { key: 'overdue_no_action', label: 'Без действий после срока', align: 'right' },
  { key: 'delay', label: 'Сверх нормы, дн.', align: 'right' },
  { key: 'on_time_pct', label: 'Уложились в норму', align: 'right' },
  { key: 'delta_overdue', label: 'Δ просроченных', align: 'right' },
];

/** Мини-полоса старения: доли корзин среди просроченных КАМа. */
export function AgingStrip({
  aging,
  className,
}: {
  aging: Record<SlaBucketKey, number>;
  className?: string;
}): ReactNode {
  const total = BUCKET_KEYS.reduce((sum, key) => sum + (aging[key] ?? 0), 0);
  if (total === 0) return null;
  const label = BUCKET_KEYS.filter((key) => (aging[key] ?? 0) > 0)
    .map((key) => `${bucketLabel(key)}: ${aging[key]}`)
    .join(', ');
  return (
    <span
      role="img"
      aria-label={`Старение: ${label}`}
      title={`Сверх нормы — ${label}`}
      className={cn('flex h-1.5 w-full max-w-[120px] overflow-hidden rounded-full bg-muted', className)}
    >
      {BUCKET_KEYS.map((key) =>
        (aging[key] ?? 0) > 0 ? (
          <span
            key={key}
            className="h-full"
            style={{ width: `${((aging[key] ?? 0) / total) * 100}%`, backgroundColor: BUCKET_COLORS[key] }}
          />
        ) : null,
      )}
    </span>
  );
}

function LoadCell({ row, median }: { row: SlaKamRow; median: number | null }): ReactNode {
  const mark = loadMark(row.active, median);
  const Icon = mark === 'above' ? MoveUp : mark === 'below' ? MoveDown : null;
  const text =
    mark === 'above'
      ? 'выше медианы'
      : mark === 'below'
        ? 'ниже медианы'
        : mark === 'median'
          ? 'на уровне медианы'
          : null;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          tabIndex={0}
          className="tabular inline-flex items-center justify-end gap-1 rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {formatInt(row.active)}
          {Icon ? <Icon className="size-3 text-muted-foreground" aria-label={text ?? undefined} /> : null}
        </span>
      </TooltipTrigger>
      <TooltipContent>
        {median !== null ? `Медиана по команде ${formatNumber1(median)}${text ? ` — ${text}` : ''}` : 'Заявок в работе на дату среза'}
        {row.inflow > 0 ? `; поступило за период: ${row.inflow}` : ''}
      </TooltipContent>
    </Tooltip>
  );
}

function OverdueCell({ row }: { row: SlaKamRow }): ReactNode {
  const pct = samplePct(row.overdue_pct, row.active);
  return (
    <span className="flex flex-col items-end gap-1">
      <span className="tabular whitespace-nowrap">
        <span className={cn(row.own_overdue > 0 && 'font-semibold')}>{ofText(row.overdue, row.active)}</span>
        {' · '}
        <span className={cn(pct.small && 'text-muted-foreground')} title={pct.hint ?? undefined}>
          {pct.text}
        </span>
      </span>
      {pct.small ? <span className="text-[11px] text-muted-foreground">мало заявок</span> : null}
      <AgingStrip aging={row.aging} />
    </span>
  );
}

function DelayCell({ row }: { row: SlaKamRow }): ReactNode {
  if (row.overdue === 0 || row.delay_avg_days === null) return <span className="text-muted-foreground">{EMPTY}</span>;
  return (
    <span className="tabular whitespace-nowrap text-sm">
      ср. {formatNumber1(row.delay_avg_days)}
      <span className="text-muted-foreground">
        {' '}
        · мед. {row.delay_median_days !== null ? formatNumber1(row.delay_median_days) : EMPTY} · макс{' '}
        {row.delay_max_days !== null ? formatNumber1(row.delay_max_days) : EMPTY} ({row.overdue})
      </span>
    </span>
  );
}

function OnTimeCell({ row }: { row: SlaKamRow }): ReactNode {
  if (row.decided === 0) return <span className="text-muted-foreground">{EMPTY}</span>;
  return (
    <span className="tabular whitespace-nowrap">
      {ofText(row.on_time, row.decided)} · {formatPct(row.on_time_pct)}
    </span>
  );
}

function DeltaCell({ row, label }: { row: SlaKamRow; label: string }): ReactNode {
  const delta = row.delta?.overdue ?? null;
  const text = deltaCaption(delta, 'count', '');
  if (!text) return <span className="text-muted-foreground">{EMPTY}</span>;
  const tone = deltaTone(delta, 'inverted');
  return (
    <span
      className={cn(
        'tabular whitespace-nowrap',
        tone === 'bad' && 'text-status-danger-deep',
        tone === 'good' && 'text-status-success-deep',
        tone === 'neutral' && 'text-muted-foreground',
      )}
      title={`${label}: было ${row.prev?.overdue ?? EMPTY}`}
    >
      {text}
    </span>
  );
}

function SortHeader({
  column,
  sort,
  onSort,
}: {
  column: (typeof COLUMNS)[number];
  sort: { key: KamSortKey; dir: SortDir };
  onSort: (key: KamSortKey) => void;
}): ReactNode {
  const active = sort.key === column.key;
  const Icon = !active ? ArrowUpDown : sort.dir === 'desc' ? ArrowDown : ArrowUp;
  return (
    <TableHead
      className={cn(column.align === 'right' && 'text-right')}
      aria-sort={active ? (sort.dir === 'desc' ? 'descending' : 'ascending') : 'none'}
    >
      <button
        type="button"
        onClick={() => onSort(column.key)}
        className={cn(
          'inline-flex items-center gap-1 rounded-sm text-left outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring',
          column.align === 'right' && 'flex-row-reverse text-right',
          active && 'text-foreground',
        )}
      >
        <span className="text-pretty">{column.label}</span>
        <Icon className="size-3 shrink-0" aria-hidden="true" />
      </button>
    </TableHead>
  );
}

export function SlaKamTable({
  kams,
  overdueItems,
  activeMedian,
  inactiveOwners,
  snapshotLabel,
  compact,
  onSelectKam,
  onOpenRequest,
}: SlaKamTableProps): ReactNode {
  const [sort, setSort] = useState<{ key: KamSortKey; dir: SortDir }>({ key: 'overdue', dir: 'desc' });
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const sorted = useMemo(
    () => [...kams].sort((a, b) => compareKamRows(a, b, sort.key, sort.dir)),
    [kams, sort],
  );
  const itemsByKam = useMemo(() => {
    const map = new Map<string, SlaOverdueItem[]>();
    for (const item of overdueItems) {
      const list = map.get(item.kam_id);
      if (list) list.push(item);
      else map.set(item.kam_id, [item]);
    }
    return map;
  }, [overdueItems]);

  const onSort = (key: KamSortKey): void =>
    setSort((prev) =>
      prev.key === key
        ? { key, dir: prev.dir === 'desc' ? 'asc' : 'desc' }
        : { key, dir: key === 'kam' ? 'asc' : 'desc' },
    );
  const toggle = (id: string): void =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const inactiveLine =
    inactiveOwners && inactiveOwners.active > 0 ? (
      <p className="px-2 pt-2 text-xs text-muted-foreground">
        У неактивных сотрудников: {inactiveOwners.active} в работе, из них {inactiveOwners.overdue} просрочено
        (в медианы и правило отбора не входят).
      </p>
    ) : null;

  if (kams.length === 0) {
    return (
      <section className="rounded-lg border border-border bg-card p-4 shadow-card sm:p-5">
        <h2 className="text-base font-semibold">КАМы</h2>
        <p className="pt-2 text-sm text-muted-foreground">У КАМов нет заявок в работе на дату среза.</p>
        {inactiveLine}
      </section>
    );
  }

  const detail = (row: SlaKamRow): ReactNode => (
    <div className="flex min-w-0 flex-col gap-2 py-2">
      <SlaOverdueList
        items={itemsByKam.get(row.kam_id) ?? []}
        onOpenRequest={onOpenRequest}
        compact={compact}
        emptyText="Просроченных заявок у КАМа нет"
      />
      <Button variant="outline" size="sm" className="self-start" onClick={() => onSelectKam(row.kam_id)}>
        Подробный разбор
        <ChevronRight aria-hidden="true" />
      </Button>
    </div>
  );

  if (compact) {
    return (
      <section className="flex min-w-0 flex-col gap-2">
        <div className="flex flex-wrap items-center justify-between gap-2 px-1">
          <h2 className="text-base font-semibold">КАМы</h2>
          <span className="text-xs text-muted-foreground">по собственным просрочкам</span>
        </div>
        <ul className="flex flex-col gap-2">
          {sorted.map((row) => {
            const open = expanded.has(row.kam_id);
            const pct = samplePct(row.overdue_pct, row.active);
            return (
              <li key={row.kam_id} className="min-w-0 rounded-lg border border-border bg-card shadow-card">
                <button
                  type="button"
                  onClick={() => toggle(row.kam_id)}
                  aria-expanded={open}
                  className="flex w-full items-start gap-3 p-3 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <KamAvatar name={row.kam} />
                  <span className="flex min-w-0 flex-1 flex-col gap-1">
                    <span className="flex flex-wrap items-center gap-1.5">
                      <span className="truncate text-sm font-medium">{row.kam}</span>
                      <KamBadges isActive={row.is_active} roles={row.roles} />
                    </span>
                    <span className="tabular text-xs text-muted-foreground">
                      Просрочено{' '}
                      <span className="font-semibold text-foreground">{ofText(row.overdue, row.active)}</span> ·{' '}
                      <span className={cn(pct.small && 'opacity-70')}>
                        {pct.text}
                        {pct.small ? ' (мало заявок)' : ''}
                      </span>
                    </span>
                    <AgingStrip aging={row.aging} />
                    <span className="tabular text-xs text-muted-foreground">
                      {row.inherited > 0 ? `при передаче ${row.inherited} · ` : ''}
                      без действий {row.overdue_no_action} · уложились{' '}
                      {row.decided > 0 ? `${ofText(row.on_time, row.decided)}` : EMPTY}
                      {row.delay_avg_days !== null ? ` · ср. +${formatNumber1(row.delay_avg_days)} дн.` : ''}
                    </span>
                  </span>
                  <ChevronDown
                    className={cn('mt-1 size-4 shrink-0 text-muted-foreground transition-transform', open && 'rotate-180')}
                    aria-hidden="true"
                  />
                </button>
                {open ? <div className="border-t border-border px-3">{detail(row)}</div> : null}
              </li>
            );
          })}
        </ul>
        {inactiveLine}
      </section>
    );
  }

  return (
    <section className="flex min-w-0 flex-col rounded-lg border border-border bg-card shadow-card">
      <header className="flex flex-wrap items-baseline justify-between gap-2 px-5 pb-2 pt-4">
        <h2 className="text-base font-semibold">КАМы</h2>
        <span className="text-xs text-muted-foreground">
          Остатки — на дату среза; «Уложились в норму» — за период; Δ — {snapshotLabel}
        </span>
      </header>
      <div className="min-w-0 px-2 pb-3 sm:px-3">
        <div className="max-w-full overflow-x-auto">
          <Table className="min-w-[1080px]">
            <TableHeader>
              <TableRow>
                {COLUMNS.map((column) => (
                  <SortHeader key={column.key} column={column} sort={sort} onSort={onSort} />
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {sorted.map((row) => {
                const open = expanded.has(row.kam_id);
                return (
                  <Fragment key={row.kam_id}>
                    <TableRow className={cn(open && 'bg-primary-tint/40')}>
                      <TableCell>
                        <span className="flex items-center gap-2">
                          <button
                            type="button"
                            onClick={() => toggle(row.kam_id)}
                            aria-expanded={open}
                            aria-label={open ? `Свернуть: ${row.kam}` : `Раскрыть просроченные: ${row.kam}`}
                            className="rounded-sm text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
                          >
                            <ChevronRight
                              className={cn('size-4 transition-transform', open && 'rotate-90')}
                              aria-hidden="true"
                            />
                          </button>
                          <KamAvatar name={row.kam} />
                          <span className="flex min-w-0 flex-col gap-0.5">
                            <button
                              type="button"
                              onClick={() => onSelectKam(row.kam_id)}
                              className="truncate rounded-sm text-left font-medium underline-offset-2 outline-none hover:text-primary hover:underline focus-visible:ring-2 focus-visible:ring-ring"
                            >
                              {row.kam}
                            </button>
                            <span className="flex flex-wrap gap-1">
                              <KamBadges isActive={row.is_active} roles={row.roles} />
                            </span>
                          </span>
                        </span>
                      </TableCell>
                      <TableCell className="text-right">
                        <LoadCell row={row} median={activeMedian} />
                      </TableCell>
                      <TableCell className="text-right">
                        <OverdueCell row={row} />
                      </TableCell>
                      <TableCell className="tabular text-right">
                        {row.inherited > 0 ? row.inherited : <span className="text-muted-foreground">{EMPTY}</span>}
                      </TableCell>
                      <TableCell className="tabular text-right">
                        {row.overdue_no_action > 0 ? (
                          <span className="font-medium text-status-danger-deep">{row.overdue_no_action}</span>
                        ) : (
                          <span className="text-muted-foreground">{EMPTY}</span>
                        )}
                      </TableCell>
                      <TableCell className="text-right">
                        <DelayCell row={row} />
                      </TableCell>
                      <TableCell className="text-right">
                        <OnTimeCell row={row} />
                      </TableCell>
                      <TableCell className="text-right">
                        <DeltaCell row={row} label={snapshotLabel} />
                      </TableCell>
                    </TableRow>
                    {open ? (
                      <TableRow className="hover:bg-transparent">
                        <TableCell colSpan={COLUMNS.length} className="bg-muted/30 px-4">
                          {detail(row)}
                        </TableCell>
                      </TableRow>
                    ) : null}
                  </Fragment>
                );
              })}
            </TableBody>
          </Table>
        </div>
        {inactiveLine}
      </div>
    </section>
  );
}
