import { ArrowLeft, ChevronDown, MoveDown, MoveUp } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import type { SlaKamReport, SlaKamStageRow, SlaLateClosedItem } from '@/shared/api/endpoints/sla';
import { Button } from '@/shared/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/shared/ui/collapsible';
import { StageTag, StatusBadge } from '@/shared/ui/data-table';
import { Skeleton } from '@/shared/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table';
import { KamAvatar, KamBadges, RequestLink } from '../KamActivityParts';
import { SlaAgingChart } from './SlaAgingChart';
import { SlaHeadline } from './SlaHeadline';
import { SlaNotes } from './SlaHowItWorks';
import { SlaKpiRow } from './SlaKpiRow';
import { SlaOverdueList } from './SlaOverdueList';
import { SlaReminders } from './SlaReminders';
import { SlaTrendChart } from './SlaTrendChart';
import type { SlaTrendMetric } from './slaCharts';
import {
  counterpartyName,
  formatDays,
  formatDelay,
  formatInt,
  formatNumber1,
  formatPct,
  loadMark,
  mskDay,
  ofText,
  onTimeCount,
  plural,
} from './slaFormat';

/**
 * Разбор КАМа: шапка, итог «У Фамилии И. О.: …», в работе относительно медианы команды,
 * 4 KPI (у «Просрочено» первым — среднее сверх нормы, рядом медиана, максимум и n),
 * старение, просроченные заявки, «Уложились в норму по этапам», тренд КАМа, опоздание
 * закрытых этапов, напоминания. Справочно «по команде» — только при широкой области.
 */

interface SlaKamViewProps {
  report: SlaKamReport | null;
  loading: boolean;
  compact: boolean;
  /** «← Все КАМы» — только при широкой области. */
  onBack?: () => void;
  metric: SlaTrendMetric;
  onMetricChange: (metric: SlaTrendMetric) => void;
  onOpenRequest: (id: string) => void;
  boardLink: string | null;
  caption: (source: string) => string;
  exportCaption: string[];
}

const WF_LABEL = { b2b: 'Вузы', b2c: 'Клиенты' } as const;

export function SlaKamView({
  report,
  loading,
  compact,
  onBack,
  metric,
  onMetricChange,
  onOpenRequest,
  boardLink,
  caption,
  exportCaption,
}: SlaKamViewProps): ReactNode {
  const navigate = useNavigate();
  const pending = loading || !report;
  const active = report?.kpi.active?.value ?? 0;
  const overdue = report?.kpi.overdue?.value ?? 0;
  const median = report?.team?.active_median ?? null;
  const mark = loadMark(active, median);
  const MarkIcon = mark === 'above' ? MoveUp : mark === 'below' ? MoveDown : null;

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        {onBack ? (
          <Button variant="ghost" size="sm" className="h-8 px-2" onClick={onBack}>
            <ArrowLeft aria-hidden="true" />
            Все КАМы
          </Button>
        ) : null}
        {report ? (
          <div className="flex min-w-0 items-center gap-2.5">
            <KamAvatar name={report.kam.name} />
            <div className="flex min-w-0 flex-col">
              <span className="flex flex-wrap items-center gap-1.5">
                <h2 className="truncate text-base font-semibold">{report.kam.name}</h2>
                <KamBadges isActive={report.kam.is_active} roles={report.kam.roles} />
              </span>
              <span className="tabular inline-flex items-center gap-1 text-xs text-muted-foreground">
                В работе {formatInt(active)}
                {median !== null ? ` (медиана по команде ${formatNumber1(median)})` : ''}
                {MarkIcon ? <MarkIcon className="size-3" aria-hidden="true" /> : null}
                {report.kpi.inflow?.value ? ` · поступило за период ${report.kpi.inflow.value}` : ''}
              </span>
            </div>
          </div>
        ) : (
          <div className="flex items-center gap-2.5" aria-hidden="true">
            <Skeleton className="size-7 rounded-full" />
            <Skeleton className="h-4 w-48 rounded-sm" />
          </div>
        )}
      </div>

      <SlaHeadline
        headline={report?.headline ?? null}
        definition={report?.definition ?? null}
        loading={pending}
      />
      <SlaKpiRow
        kpi={report?.kpi ?? null}
        comparison={report?.comparison ?? null}
        loading={pending}
        variant="kam"
        team={report?.team ?? null}
        onOverdueClick={boardLink ? () => navigate(boardLink) : undefined}
      />

      <div className="grid grid-cols-12 items-stretch gap-4">
        <div className="col-span-12 min-w-0 lg:col-span-5">
          <SlaAgingChart
            aging={report?.aging ?? []}
            snapshotLabel={report?.comparison.snapshot_label ?? ''}
            caption={caption}
            exportCaption={exportCaption}
            compact={compact}
            loading={pending}
          />
        </div>
        <div className="col-span-12 min-w-0 lg:col-span-7">
          <OnTimeByStage stages={report?.stages ?? []} loading={pending} />
        </div>
      </div>

      <section className="flex min-w-0 flex-col gap-2 rounded-lg border border-border bg-card p-3 shadow-card sm:p-4">
        <header className="flex flex-wrap items-baseline justify-between gap-2 px-1">
          <h2 className="text-base font-semibold">Просроченные заявки</h2>
          <span className="text-xs text-muted-foreground">
            {pending ? 'Загрузка…' : `${overdue} · по убыванию задержки сверх нормы`}
          </span>
        </header>
        {pending ? (
          <div className="flex flex-col gap-2" aria-hidden="true">
            <Skeleton className="h-10 rounded-md" />
            <Skeleton className="h-10 rounded-md" />
            <Skeleton className="h-10 rounded-md" />
          </div>
        ) : (
          <SlaOverdueList
            items={report.overdue.items}
            total={report.overdue.total}
            truncated={report.overdue.truncated}
            onOpenRequest={onOpenRequest}
            compact={compact}
            emptyText="Просроченных заявок нет."
          />
        )}
      </section>

      <SlaTrendChart
        chartId="sla-kam-trend"
        title="Тренд КАМа"
        trend={report?.trend ?? null}
        metric={metric}
        onMetricChange={onMetricChange}
        caption={caption}
        exportCaption={exportCaption}
        compact={compact}
        loading={pending}
      />

      {report && report.late_closed.items.length > 0 ? (
        <LateClosed items={report.late_closed.items} total={report.late_closed.total} compact={compact} onOpenRequest={onOpenRequest} />
      ) : null}

      {report ? (
        <SlaReminders
          reminders={report.reminders}
          comparison={report.comparison}
          team={report.team}
          compact={compact}
          onOpenRequest={onOpenRequest}
        />
      ) : null}

      {report ? <SlaNotes notes={report.notes} /> : null}
    </div>
  );
}

/** «Уложились в норму по этапам»: 100-процентные полосы с числами. */
function OnTimeByStage({ stages, loading }: { stages: SlaKamStageRow[]; loading: boolean }): ReactNode {
  const rows = stages.filter((row) => row.decided > 0);
  const workflows = new Set(rows.map((row) => row.workflow_type));
  return (
    <section className="flex h-full min-w-0 flex-col rounded-lg border border-border bg-card shadow-card">
      <header className="px-5 pb-2 pt-4">
        <h2 className="text-base font-semibold">Уложились в норму по этапам</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Этапы с решением в периоде: ушли до срока или срок истёк
        </p>
      </header>
      <div className="min-w-0 flex-1 px-5 pb-4">
        {loading ? (
          <div className="flex flex-col gap-3" aria-hidden="true">
            <Skeleton className="h-6 rounded-sm" />
            <Skeleton className="h-6 rounded-sm" />
            <Skeleton className="h-6 rounded-sm" />
          </div>
        ) : rows.length === 0 ? (
          <p className="py-4 text-sm text-muted-foreground">
            За период этапы не завершались и сроки не истекали.
          </p>
        ) : (
          <ul className="flex flex-col gap-3">
            {rows.map((row) => {
              const onTime = onTimeCount(row);
              const late = Math.max(0, row.decided - onTime);
              const onTimeShare = (onTime / row.decided) * 100;
              return (
                <li key={`${row.workflow_type}:${row.id}`} className="flex min-w-0 flex-col gap-1">
                  <span className="flex flex-wrap items-center justify-between gap-x-2 gap-y-0.5 text-sm">
                    <span className="flex min-w-0 items-center gap-1.5">
                      {workflows.size > 1 ? (
                        <span className="text-xs text-muted-foreground">{WF_LABEL[row.workflow_type]}</span>
                      ) : null}
                      <StageTag size="sm" color={row.color} name={row.name} />
                      <span className="text-xs text-muted-foreground">норма {formatDays(row.norm_days)}</span>
                    </span>
                    <span className="tabular text-xs">
                      {ofText(onTime, row.decided)} · {formatPct(row.on_time_pct)}
                    </span>
                  </span>
                  <span
                    role="img"
                    aria-label={`Уложились ${onTime}, не уложились ${late}`}
                    className="flex h-2.5 w-full overflow-hidden rounded-full bg-muted"
                  >
                    {onTime > 0 ? (
                      <span className="h-full bg-status-success" style={{ width: `${onTimeShare}%` }} />
                    ) : null}
                    {late > 0 ? (
                      <span className="h-full bg-status-warning" style={{ width: `${100 - onTimeShare}%` }} />
                    ) : null}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
        {!loading && rows.length > 0 ? (
          <p className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1.5">
              <span className="size-2 rounded-full bg-status-success" aria-hidden="true" />
              уложились
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="size-2 rounded-full bg-status-warning" aria-hidden="true" />
              не уложились
            </span>
          </p>
        ) : null}
      </div>
    </section>
  );
}

function exitedText(item: SlaLateClosedItem): string {
  if (item.exited_to.outcome === 'won') return `${item.exited_to.name} (выигрыш)`;
  if (item.exited_to.outcome === 'lost') return `${item.exited_to.name} (отказ)`;
  if (item.exited_to.is_return) return `${item.exited_to.name} (возврат)`;
  return item.exited_to.name;
}

/** «Опоздание закрытых этапов» — сворачиваемая таблица. */
function LateClosed({
  items,
  total,
  compact,
  onOpenRequest,
}: {
  items: SlaLateClosedItem[];
  total: number;
  compact: boolean;
  onOpenRequest: (id: string) => void;
}): ReactNode {
  const [open, setOpen] = useState(false);
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <section className="flex min-w-0 flex-col gap-2 rounded-lg border border-border bg-card p-3 shadow-card sm:p-4">
        <CollapsibleTrigger asChild>
          <button
            type="button"
            className="flex w-full items-center justify-between gap-2 rounded-sm px-1 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <span className="flex flex-col">
              <span className="text-base font-semibold">Опоздание закрытых этапов</span>
              <span className="text-xs text-muted-foreground">
                Закрыто в периоде позже нормы: {total} {plural(total, ['этап', 'этапа', 'этапов'])}
              </span>
            </span>
            <ChevronDown
              className={open ? 'size-4 rotate-180 transition-transform' : 'size-4 transition-transform'}
              aria-hidden="true"
            />
          </button>
        </CollapsibleTrigger>
        <CollapsibleContent>
          {compact ? (
            <ul className="flex flex-col gap-2">
              {items.map((item) => (
                <li
                  key={`${item.request_id}:${item.entered}`}
                  className="flex min-w-0 flex-col gap-1 rounded-md border border-border p-3"
                >
                  <RequestLink request={item} onOpen={onOpenRequest} className="text-sm" />
                  <span className="text-xs text-muted-foreground">
                    {counterpartyName(item.university, item.client)}
                  </span>
                  <span className="flex flex-wrap items-center gap-1.5">
                    <StageTag size="sm" color={item.stage.color} name={item.stage.name} />
                    <StatusBadge status="warning" size="sm">
                      {formatDelay(item.delay_days)}
                    </StatusBadge>
                    {item.inherited ? (
                      <StatusBadge status="draft" size="sm">
                        получена просроченной
                      </StatusBadge>
                    ) : null}
                  </span>
                  <span className="tabular text-xs text-muted-foreground">
                    {mskDay(item.entered)} — {mskDay(item.left_at)} · {formatDays(item.duration_days)} при норме{' '}
                    {formatDays(item.norm_days)} · → {exitedText(item)}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <div className="max-w-full overflow-x-auto">
              <Table className="min-w-[900px]">
                <TableHeader>
                  <TableRow>
                    <TableHead>Заявка</TableHead>
                    <TableHead>Этап</TableHead>
                    <TableHead>Вход</TableHead>
                    <TableHead>Выход</TableHead>
                    <TableHead className="text-right">Длительность</TableHead>
                    <TableHead className="text-right">Норма</TableHead>
                    <TableHead className="text-right">Опоздание</TableHead>
                    <TableHead>Куда вышла</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((item) => (
                    <TableRow key={`${item.request_id}:${item.entered}`}>
                      <TableCell className="max-w-[260px]">
                        <span className="flex flex-col gap-0.5">
                          <RequestLink request={item} onOpen={onOpenRequest} className="text-sm" />
                          <span className="text-xs text-muted-foreground">
                            {counterpartyName(item.university, item.client)}
                          </span>
                          {item.inherited ? (
                            <StatusBadge status="draft" size="sm">
                              получена просроченной
                            </StatusBadge>
                          ) : null}
                        </span>
                      </TableCell>
                      <TableCell>
                        <StageTag size="sm" color={item.stage.color} name={item.stage.name} />
                      </TableCell>
                      <TableCell className="tabular">{mskDay(item.entered)}</TableCell>
                      <TableCell className="tabular">{mskDay(item.left_at)}</TableCell>
                      <TableCell className="tabular text-right">{formatDays(item.duration_days)}</TableCell>
                      <TableCell className="tabular text-right">{formatDays(item.norm_days)}</TableCell>
                      <TableCell className="tabular text-right font-medium text-status-warning-deep">
                        {formatDelay(item.delay_days)}
                      </TableCell>
                      <TableCell>{exitedText(item)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CollapsibleContent>
      </section>
    </Collapsible>
  );
}
