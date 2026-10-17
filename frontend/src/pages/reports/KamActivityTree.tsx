import { useQuery } from '@tanstack/react-query';
import { ChevronRight, ChevronsDownUp, ChevronsUpDown, Flame } from 'lucide-react';
import { Fragment, useMemo, useState, type KeyboardEvent, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import {
  getKamActivity,
  type KamActivityKam,
  type KamActivityParams,
  type KamActivityReport,
  type KamActivityRequest,
  type KamActivityUniversity,
} from '@/shared/api/endpoints/kamActivity';
import { cn } from '@/shared/lib/cn';
import { Button } from '@/shared/ui/button';
import { StatusBadge } from '@/shared/ui/data-table';
import { Skeleton } from '@/shared/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/ui/tooltip';
import {
  DaysOnStage,
  InteractionsCell,
  KamAvatar,
  KamBadges,
  LastContact,
  OutcomeBadge,
  RequestLink,
  RequestStageCell,
  StageChips,
} from './KamActivityParts';
import { plural } from './kamActivityModel';

/**
 * Дерево-таблица «КАМ → вузы → заявки» для десктопа (≥ md, ux.md §6.4):
 * строки-переключатели — кнопки с aria-expanded/aria-controls, у строк aria-level;
 * «проблемные сначала» (порядок с сервера). Вуз с усечёнными заявками догружает
 * их тем же эндпоинтом с kam_id + university_id.
 */

export function kamKey(kam: KamActivityKam): string {
  return `kam:${kam.kam_id}`;
}

export function uniKey(kam: KamActivityKam, uni: KamActivityUniversity): string {
  return `uni:${kam.kam_id}:${uni.university_id ?? 'b2c'}`;
}

/** Ключи всех раскрываемых строк (для «Развернуть всё»). */
export function allExpandableKeys(report: KamActivityReport): string[] {
  const keys: string[] = [];
  for (const kam of report.rows) {
    if (kam.universities.length > 0) keys.push(kamKey(kam));
    for (const uni of kam.universities) {
      if (uni.requests_count > 0) keys.push(uniKey(kam, uni));
    }
  }
  return keys;
}

/** По умолчанию: один КАМ в отчёте — он раскрыт, иначе всё свёрнуто. */
export function defaultExpanded(report: KamActivityReport): Set<string> {
  const only = report.rows.length === 1 ? report.rows[0] : undefined;
  return new Set(only && only.universities.length > 0 ? [kamKey(only)] : []);
}

export function useExpanded(report: KamActivityReport): {
  expanded: Set<string>;
  toggle: (key: string) => void;
  expandAll: () => void;
  collapseAll: () => void;
} {
  const resetKey = `${report.generated_at}|${report.rows.length}`;
  const [state, setState] = useState<{ key: string; expanded: Set<string> }>(() => ({
    key: resetKey,
    expanded: defaultExpanded(report),
  }));
  // новый отчёт (другой период/фильтры) — состояние раскрытия по умолчанию
  const expanded = state.key === resetKey ? state.expanded : defaultExpanded(report);
  return {
    expanded,
    toggle: (key) =>
      setState(() => {
        const next = new Set(expanded);
        if (next.has(key)) next.delete(key);
        else next.add(key);
        return { key: resetKey, expanded: next };
      }),
    expandAll: () => setState({ key: resetKey, expanded: new Set(allExpandableKeys(report)) }),
    collapseAll: () => setState({ key: resetKey, expanded: new Set() }),
  };
}

export function ExpandControls({
  onExpand,
  onCollapse,
}: {
  onExpand: () => void;
  onCollapse: () => void;
}): ReactNode {
  return (
    <div className="flex items-center gap-1">
      <Button variant="ghost" size="sm" onClick={onExpand}>
        <ChevronsUpDown aria-hidden="true" />
        Развернуть всё
      </Button>
      <Button variant="ghost" size="sm" onClick={onCollapse}>
        <ChevronsDownUp aria-hidden="true" />
        Свернуть всё
      </Button>
    </div>
  );
}

function ToggleButton({
  expanded,
  controls,
  label,
  onToggle,
}: {
  expanded: boolean;
  controls: string;
  label: string;
  onToggle: () => void;
}): ReactNode {
  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>): void => {
    if (event.key === 'ArrowRight' && !expanded) onToggle();
    if (event.key === 'ArrowLeft' && expanded) onToggle();
  };
  return (
    <button
      type="button"
      aria-expanded={expanded}
      aria-controls={controls}
      aria-label={`${expanded ? 'Свернуть' : 'Развернуть'}: ${label}`}
      onClick={onToggle}
      onKeyDown={onKeyDown}
      className="inline-flex size-6 shrink-0 items-center justify-center rounded-sm text-muted-foreground outline-none hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
    >
      <ChevronRight
        className={cn('size-4 transition-transform duration-150', expanded && 'rotate-90')}
        aria-hidden="true"
      />
    </button>
  );
}

function rowId(prefix: string, key: string): string {
  return `${prefix}-${key.replace(/[^a-zA-Z0-9-]/g, '-')}`;
}

interface TreeContext {
  report: KamActivityReport;
  params: KamActivityParams;
  onOpenRequest: (id: string) => void;
}

function RequestRow({
  request,
  context,
  id,
}: {
  request: KamActivityRequest;
  context: TreeContext;
  id: string;
}): ReactNode {
  const { period } = context.report;
  return (
    <TableRow id={id} aria-level={3} className="bg-muted/30">
      <TableCell className="py-2 pl-[4.25rem]">
        <div className="flex min-w-0 flex-col gap-0.5">
          <RequestLink request={request} onOpen={context.onOpenRequest} className="text-sm" />
          {request.client ? (
            <span className="text-xs text-muted-foreground">{request.client}</span>
          ) : null}
        </div>
      </TableCell>
      <TableCell className="text-muted-foreground">—</TableCell>
      <TableCell>
        <span className="flex flex-wrap items-center gap-1">
          <OutcomeBadge outcome={request.closed_in_period} />
          {request.created_in_period ? (
            <StatusBadge status="progress" size="sm">
              Новая
            </StatusBadge>
          ) : null}
          {!request.closed_in_period && !request.created_in_period ? (
            <span className="text-muted-foreground">—</span>
          ) : null}
        </span>
      </TableCell>
      <TableCell>
        <RequestStageCell request={request} />
      </TableCell>
      <TableCell>
        <DaysOnStage request={request} />
      </TableCell>
      <TableCell>
        <InteractionsCell interactions={request.interactions} />
      </TableCell>
      <TableCell>
        <LastContact value={request.last_contact_at} asOf={period.as_of} periodFrom={period.from} />
      </TableCell>
    </TableRow>
  );
}

/** Догрузка заявок вуза, опущенных предохранителем размера дерева. */
function TruncatedRequests({
  kam,
  uni,
  context,
  idPrefix,
}: {
  kam: KamActivityKam;
  uni: KamActivityUniversity;
  context: TreeContext;
  idPrefix: string;
}): ReactNode {
  const params: KamActivityParams = {
    ...context.params,
    kam_id: [kam.kam_id],
    university_id: uni.university_id ? [uni.university_id] : undefined,
  };
  const query = useQuery({
    queryKey: ['kam-activity', params],
    queryFn: ({ signal }) => getKamActivity(params, signal),
    staleTime: 30_000,
  });
  if (query.isLoading) {
    return (
      <>
        {Array.from({ length: Math.min(uni.requests_count, 4) }, (_, index) => (
          <TableRow key={index} aria-hidden="true">
            <TableCell colSpan={7} className="py-2 pl-[4.25rem]">
              <Skeleton className="h-4 w-2/3" />
            </TableCell>
          </TableRow>
        ))}
      </>
    );
  }
  const node = query.data?.rows
    .find((row) => row.kam_id === kam.kam_id)
    ?.universities.find((item) => item.university_id === uni.university_id);
  return (
    <>
      {(node?.requests ?? []).map((request) => (
        <RequestRow
          key={request.request_id}
          id={`${idPrefix}-${request.request_id}`}
          request={request}
          context={context}
        />
      ))}
    </>
  );
}

function UniversityRows({
  kam,
  uni,
  context,
  expanded,
  onToggle,
  id,
}: {
  kam: KamActivityKam;
  uni: KamActivityUniversity;
  context: TreeContext;
  expanded: boolean;
  onToggle: () => void;
  id: string;
}): ReactNode {
  const { period } = context.report;
  const key = uniKey(kam, uni);
  const childPrefix = rowId('req', key);
  const childIds = uni.requests.map((request) => `${childPrefix}-${request.request_id}`).join(' ');
  const untouched = uni.assigned && uni.interactions.total === 0;
  return (
    <>
      <TableRow id={id} aria-level={2} aria-expanded={uni.requests_count > 0 ? expanded : undefined}>
        <TableCell className="py-2 pl-8">
          <div className="flex min-w-0 items-start gap-1.5">
            {uni.requests_count > 0 ? (
              <ToggleButton
                expanded={expanded}
                controls={childIds || childPrefix}
                label={uni.name}
                onToggle={onToggle}
              />
            ) : (
              <span className="size-6 shrink-0" aria-hidden="true" />
            )}
            <div className="flex min-w-0 flex-col gap-1">
              {uni.university_id ? (
                <Link
                  to={`/registry/universities/${uni.university_id}`}
                  className="truncate rounded-sm text-sm font-medium underline-offset-2 outline-none hover:text-primary hover:underline focus-visible:ring-2 focus-visible:ring-ring"
                  title={uni.name}
                >
                  {uni.short_name || uni.name}
                </Link>
              ) : (
                <span className="text-sm font-medium text-muted-foreground">{uni.name}</span>
              )}
              <span className="flex flex-wrap items-center gap-1">
                {uni.assigned ? (
                  <StatusBadge status="progress" size="sm">
                    Закреплён
                  </StatusBadge>
                ) : null}
                {untouched ? (
                  <StatusBadge status="warning" size="sm">
                    Нет контакта за период
                  </StatusBadge>
                ) : null}
              </span>
            </div>
          </div>
        </TableCell>
        <TableCell className="text-muted-foreground">—</TableCell>
        <TableCell>
          <span className="tabular inline-flex items-center gap-1.5">
            {uni.requests_count}
            {uni.stuck > 0 ? (
              <span className="inline-flex items-center gap-0.5 text-xs text-status-warning-deep">
                <Flame className="size-3" aria-hidden="true" />
                {uni.stuck}
                <span className="sr-only">зависших</span>
              </span>
            ) : null}
          </span>
        </TableCell>
        <TableCell>
          <StageChips stages={uni.stages} />
        </TableCell>
        <TableCell className="text-muted-foreground">—</TableCell>
        <TableCell>
          <InteractionsCell interactions={uni.interactions} />
        </TableCell>
        <TableCell>
          <LastContact value={uni.last_contact_at} asOf={period.as_of} periodFrom={period.from} />
        </TableCell>
      </TableRow>
      {expanded ? (
        uni.requests_truncated ? (
          <TruncatedRequests kam={kam} uni={uni} context={context} idPrefix={childPrefix} />
        ) : (
          uni.requests.map((request) => (
            <RequestRow
              key={request.request_id}
              id={`${childPrefix}-${request.request_id}`}
              request={request}
              context={context}
            />
          ))
        )
      ) : null}
    </>
  );
}

function KamRow({
  kam,
  context,
  expanded,
  onToggle,
}: {
  kam: KamActivityKam;
  context: TreeContext;
  expanded: boolean;
  onToggle: () => void;
}): ReactNode {
  const { period } = context.report;
  const key = kamKey(kam);
  const childIds = kam.universities.map((uni) => rowId('uni', uniKey(kam, uni))).join(' ');
  return (
    <TableRow
      aria-level={1}
      aria-expanded={kam.universities.length > 0 ? expanded : undefined}
      className="bg-card font-medium"
    >
      <TableCell className="py-2.5">
        <div className="flex min-w-0 items-center gap-1.5">
          {kam.universities.length > 0 ? (
            <ToggleButton expanded={expanded} controls={childIds || rowId('uni', key)} label={kam.kam} onToggle={onToggle} />
          ) : (
            <span className="size-6 shrink-0" aria-hidden="true" />
          )}
          <KamAvatar name={kam.kam} />
          <span className="min-w-0 truncate" title={kam.kam}>
            {kam.kam}
          </span>
          <KamBadges isActive={kam.is_active} roles={kam.roles} />
        </div>
      </TableCell>
      <TableCell>
        <Tooltip>
          <TooltipTrigger asChild>
            <span tabIndex={0} className="tabular whitespace-nowrap rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring">
              {kam.universities_assigned > 0
                ? `${kam.assigned_touched} из ${kam.universities_assigned}`
                : kam.universities_touched}
            </span>
          </TooltipTrigger>
          <TooltipContent>
            С контактом {kam.assigned_touched} из {kam.universities_assigned} закреплённых; всего вузов
            в отчёте {kam.universities_count}, с взаимодействиями {kam.universities_touched}
          </TooltipContent>
        </Tooltip>
      </TableCell>
      <TableCell>
        <span className="tabular flex flex-col leading-tight">
          {kam.requests_count}
          {kam.new_requests > 0 ? (
            <span className="whitespace-nowrap text-xs font-normal text-muted-foreground">
              +{kam.new_requests} {plural(kam.new_requests, ['новая', 'новые', 'новых'])}
            </span>
          ) : null}
        </span>
      </TableCell>
      <TableCell>
        <StageChips stages={kam.stages} limit={3} />
      </TableCell>
      <TableCell>
        {kam.stuck > 0 ? (
          <span className="tabular inline-flex items-center gap-1 text-sm text-status-warning-deep">
            <Flame className="size-3.5" aria-hidden="true" />
            {kam.stuck} завис.
          </span>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </TableCell>
      <TableCell>
        <InteractionsCell interactions={kam.interactions} />
      </TableCell>
      <TableCell>
        <LastContact value={kam.last_contact_at} asOf={period.as_of} periodFrom={period.from} />
      </TableCell>
    </TableRow>
  );
}

export function KamActivityTree({
  report,
  params,
  onOpenRequest,
}: {
  report: KamActivityReport;
  params: KamActivityParams;
  onOpenRequest: (id: string) => void;
}): ReactNode {
  const { expanded, toggle, expandAll, collapseAll } = useExpanded(report);
  const context = useMemo<TreeContext>(
    () => ({ report, params, onOpenRequest }),
    [report, params, onOpenRequest],
  );
  return (
    <section className="flex flex-col gap-2 rounded-lg border border-border bg-card shadow-card">
      <header className="flex flex-wrap items-center justify-between gap-2 px-4 pt-3">
        <h2 className="text-base font-semibold">КАМ → вузы → заявки</h2>
        <ExpandControls onExpand={expandAll} onCollapse={collapseAll} />
      </header>
      <Table role="treegrid" aria-label="Активность КАМов: КАМ, вузы, заявки" className="min-w-[960px]">
        <TableHeader>
          <TableRow>
            <TableHead className="w-[30%]">Название</TableHead>
            <TableHead>Вузы</TableHead>
            <TableHead>Заявки</TableHead>
            <TableHead className="w-[22%]">На каком этапе остановились</TableHead>
            <TableHead>Дней на этапе</TableHead>
            <TableHead>Взаимодействия</TableHead>
            <TableHead>Последний контакт</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {report.rows.map((kam) => {
            const kamOpen = expanded.has(kamKey(kam));
            return (
              <Fragment key={kam.kam_id}>
                <KamRow
                  kam={kam}
                  context={context}
                  expanded={kamOpen}
                  onToggle={() => toggle(kamKey(kam))}
                />
                {kamOpen
                  ? kam.universities.map((uni) => (
                      <UniversityRows
                        key={uniKey(kam, uni)}
                        id={rowId('uni', uniKey(kam, uni))}
                        kam={kam}
                        uni={uni}
                        context={context}
                        expanded={expanded.has(uniKey(kam, uni))}
                        onToggle={() => toggle(uniKey(kam, uni))}
                      />
                    ))
                  : null}
              </Fragment>
            );
          })}
        </TableBody>
      </Table>
    </section>
  );
}
