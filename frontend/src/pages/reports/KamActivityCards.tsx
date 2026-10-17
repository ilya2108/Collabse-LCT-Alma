import { useQuery } from '@tanstack/react-query';
import { ChevronDown, Flame } from 'lucide-react';
import type { ReactNode } from 'react';
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
import { StatusBadge } from '@/shared/ui/data-table';
import { Skeleton } from '@/shared/ui/skeleton';
import {
  DaysOnStage,
  InteractionsCell,
  InteractionsText,
  KamAvatar,
  KamBadges,
  LastContact,
  OutcomeBadge,
  RequestLink,
  RequestStageCell,
  StageChips,
} from './KamActivityParts';
import { ExpandControls, kamKey, uniKey, useExpanded } from './KamActivityTree';

/**
 * Мобильная детализация (< md, ux.md §6.4): аккордеон карточек
 * «КАМ → вузы → заявки» на тех же данных, что дерево-таблица; ширина 375 px без
 * горизонтальной прокрутки страницы.
 */

function Metric({ label, value }: { label: string; value: ReactNode }): ReactNode {
  return (
    <div className="flex min-w-0 flex-col">
      <span className="text-[11px] text-muted-foreground">{label}</span>
      <span className="tabular truncate text-sm font-semibold">{value}</span>
    </div>
  );
}

function RequestCardItem({
  request,
  report,
  onOpenRequest,
}: {
  request: KamActivityRequest;
  report: KamActivityReport;
  onOpenRequest: (id: string) => void;
}): ReactNode {
  return (
    <li className="flex flex-col gap-1.5 rounded-md border border-border bg-card p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <RequestLink request={request} onOpen={onOpenRequest} className="min-w-0 text-sm" />
        <OutcomeBadge outcome={request.closed_in_period} />
      </div>
      {request.client ? <span className="text-xs text-muted-foreground">{request.client}</span> : null}
      <RequestStageCell request={request} />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <DaysOnStage request={request} />
        <InteractionsCell interactions={request.interactions} />
      </div>
      <LastContact
        value={request.last_contact_at}
        asOf={report.period.as_of}
        periodFrom={report.period.from}
      />
    </li>
  );
}

function TruncatedList({
  kam,
  uni,
  report,
  params,
  onOpenRequest,
}: {
  kam: KamActivityKam;
  uni: KamActivityUniversity;
  report: KamActivityReport;
  params: KamActivityParams;
  onOpenRequest: (id: string) => void;
}): ReactNode {
  const scoped: KamActivityParams = {
    ...params,
    kam_id: [kam.kam_id],
    university_id: uni.university_id ? [uni.university_id] : undefined,
  };
  const query = useQuery({
    queryKey: ['kam-activity', scoped],
    queryFn: ({ signal }) => getKamActivity(scoped, signal),
    staleTime: 30_000,
  });
  if (query.isLoading) {
    return (
      <li aria-hidden="true">
        <Skeleton className="h-16 w-full" />
      </li>
    );
  }
  const node = query.data?.rows
    .find((row) => row.kam_id === kam.kam_id)
    ?.universities.find((item) => item.university_id === uni.university_id);
  return (
    <>
      {(node?.requests ?? []).map((request) => (
        <RequestCardItem
          key={request.request_id}
          request={request}
          report={report}
          onOpenRequest={onOpenRequest}
        />
      ))}
    </>
  );
}

function ToggleHeader({
  expanded,
  onToggle,
  controls,
  disabled,
  children,
}: {
  expanded: boolean;
  onToggle: () => void;
  controls: string;
  disabled?: boolean;
  children: ReactNode;
}): ReactNode {
  return (
    <button
      type="button"
      aria-expanded={disabled ? undefined : expanded}
      aria-controls={disabled ? undefined : controls}
      disabled={disabled}
      onClick={onToggle}
      className="flex w-full min-w-0 items-start gap-2 rounded-md text-left outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-default"
    >
      <span className="min-w-0 flex-1">{children}</span>
      {disabled ? null : (
        <ChevronDown
          className={cn(
            'mt-1 size-4 shrink-0 text-muted-foreground transition-transform duration-150',
            expanded && 'rotate-180',
          )}
          aria-hidden="true"
        />
      )}
    </button>
  );
}

export function KamActivityCards({
  report,
  params,
  onOpenRequest,
}: {
  report: KamActivityReport;
  params: KamActivityParams;
  onOpenRequest: (id: string) => void;
}): ReactNode {
  const { expanded, toggle, expandAll, collapseAll } = useExpanded(report);
  const { period } = report;
  return (
    <section className="flex flex-col gap-2" aria-label="Активность КАМов: КАМ, вузы, заявки">
      <div className="flex justify-end">
        <ExpandControls onExpand={expandAll} onCollapse={collapseAll} />
      </div>
      <ul className="flex flex-col gap-2">
        {report.rows.map((kam) => {
          const key = kamKey(kam);
          const open = expanded.has(key);
          const listId = `cards-${kam.kam_id}`;
          return (
            <li key={kam.kam_id} className="rounded-lg border border-border bg-card p-3 shadow-card">
              <ToggleHeader
                expanded={open}
                onToggle={() => toggle(key)}
                controls={listId}
                disabled={kam.universities.length === 0}
              >
                <span className="flex min-w-0 items-center gap-2">
                  <KamAvatar name={kam.kam} />
                  <span className="min-w-0 truncate font-medium">{kam.kam}</span>
                </span>
                <span className="mt-1 flex flex-wrap gap-1">
                  <KamBadges isActive={kam.is_active} roles={kam.roles} />
                </span>
                <span className="mt-2 grid grid-cols-3 gap-2">
                  <Metric
                    label="Вузы с контактом"
                    value={
                      kam.universities_assigned > 0
                        ? `${kam.assigned_touched} из ${kam.universities_assigned}`
                        : kam.universities_touched
                    }
                  />
                  <Metric label="Взаимодействий" value={kam.interactions.total} />
                  <Metric
                    label="Зависших"
                    value={
                      kam.stuck > 0 ? (
                        <span className="inline-flex items-center gap-0.5 text-status-warning-deep">
                          <Flame className="size-3.5" aria-hidden="true" />
                          {kam.stuck}
                        </span>
                      ) : (
                        0
                      )
                    }
                  />
                </span>
              </ToggleHeader>
              {open ? (
                <ul id={listId} className="mt-3 flex flex-col gap-2">
                  {kam.universities.map((uni) => {
                    const ukey = uniKey(kam, uni);
                    const uniOpen = expanded.has(ukey);
                    const requestsId = `cards-${kam.kam_id}-${uni.university_id ?? 'b2c'}`;
                    const untouched = uni.assigned && uni.interactions.total === 0;
                    return (
                      <li key={ukey} className="rounded-md border border-border bg-muted/30 p-2.5">
                        <ToggleHeader
                          expanded={uniOpen}
                          onToggle={() => toggle(ukey)}
                          controls={requestsId}
                          disabled={uni.requests_count === 0}
                        >
                          <span className="block truncate text-sm font-medium">
                            {uni.short_name || uni.name}
                          </span>
                          <span className="mt-1 flex flex-wrap gap-1">
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
                          <span className="mt-1.5 block">
                            <StageChips stages={uni.stages} limit={3} />
                          </span>
                          <span className="mt-1.5 flex flex-wrap items-center justify-between gap-2 text-sm">
                            <span className="tabular text-muted-foreground">
                              Заявок: {uni.requests_count}
                            </span>
                            <InteractionsText interactions={uni.interactions} />
                          </span>
                          <span className="mt-1 block">
                            <LastContact
                              value={uni.last_contact_at}
                              asOf={period.as_of}
                              periodFrom={period.from}
                            />
                          </span>
                        </ToggleHeader>
                        {uni.university_id ? (
                          <Link
                            to={`/registry/universities/${uni.university_id}`}
                            className="mt-1 inline-block text-xs text-primary underline-offset-2 hover:underline"
                          >
                            Карточка вуза
                          </Link>
                        ) : null}
                        {uniOpen ? (
                          <ul id={requestsId} className="mt-2 flex flex-col gap-2">
                            {uni.requests_truncated ? (
                              <TruncatedList
                                kam={kam}
                                uni={uni}
                                report={report}
                                params={params}
                                onOpenRequest={onOpenRequest}
                              />
                            ) : (
                              uni.requests.map((request) => (
                                <RequestCardItem
                                  key={request.request_id}
                                  request={request}
                                  report={report}
                                  onOpenRequest={onOpenRequest}
                                />
                              ))
                            )}
                          </ul>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
              ) : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
