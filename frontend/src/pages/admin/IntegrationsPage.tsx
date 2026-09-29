import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, ArrowRight, Braces, RotateCw } from 'lucide-react';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useOnboarding } from '@/features/onboarding';
import {
  listIntegrationEvents,
  retryIntegrationEvent,
  type IntegrationEventFilters,
} from '@/shared/api/endpoints/integrationEvents';
import type { IntegrationEventRecord, ListEnvelope } from '@/shared/api/types';
import { useAuth } from '@/shared/auth/AuthContext';
import { cn } from '@/shared/lib/cn';
import { dayjs } from '@/shared/lib/dayjs';
import { formatDateTime } from '@/shared/lib/format';
import { toastError, toastSuccess } from '@/shared/lib/toast';
import { Button } from '@/shared/ui/button';
import {
  DataTable,
  FilterSelect,
  PageHeader,
  StaggerGrid,
  StaggerItem,
  StatusBadge,
  type DataTableColumn,
  type RegistryFetchParams,
} from '@/shared/ui/data-table';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/shared/ui/dialog';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/ui/tooltip';
import type { SemanticStatus } from '@/shared/config/tokens';

/**
 * Монитор интеграций LMS/CMS (ux.md §14.5, redesign.md §6.9): две
 * карточки-коннектора со статус-точкой (пульс у живого) и счётчиками
 * «→ N / ← M», журнал обмена на DataTable с телами JSON и повтором из DLQ.
 * Обновление журнала — поллинг 15 с (инвалидация запроса DataTable).
 */

const STATUS_META: Record<string, { label: string; status: SemanticStatus }> = {
  pending: { label: 'В очереди', status: 'draft' },
  retrying: { label: 'Повторяем', status: 'warning' },
  delivered: { label: 'Доставлено', status: 'success' },
  processed: { label: 'Обработано', status: 'success' },
  dead: { label: 'DLQ', status: 'danger' },
  skipped: { label: 'Пропущено', status: 'draft' },
};

function statusBadge(status: string): ReactNode {
  const meta = STATUS_META[status] ?? { label: status, status: 'draft' as SemanticStatus };
  return <StatusBadge status={meta.status}>{meta.label}</StatusBadge>;
}

interface ConnectorCardProps {
  system: 'lms' | 'cms';
  title: string;
  events: IntegrationEventRecord[];
}

function ConnectorCard({ system, title, events }: ConnectorCardProps): ReactNode {
  const dayAgo = dayjs().subtract(24, 'hour');
  const recent = events.filter((e) => e.system === system && dayjs(e.created_at).isAfter(dayAgo));
  const sent = recent.filter((e) => e.direction === 'outbound').length;
  const received = recent.filter((e) => e.direction === 'inbound').length;
  const dead = events.filter((e) => e.system === system && e.status === 'dead').length;
  const alive = dead === 0;
  return (
    <div className="rounded-lg border bg-card p-5 shadow-card">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-base font-semibold">
          <span
            className={cn(
              'size-2 rounded-full motion-safe:animate-pulse',
              alive ? 'bg-status-success' : 'bg-status-danger',
            )}
            aria-hidden="true"
          />
          {title}
        </h2>
        {alive ? (
          <StatusBadge status="success" size="sm">
            ОК
          </StatusBadge>
        ) : (
          <StatusBadge status="danger" size="sm">
            DLQ: {dead}
          </StatusBadge>
        )}
      </div>
      <div className="flex items-center gap-6">
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <span className="flex size-7 items-center justify-center rounded-full bg-primary-tint text-primary" aria-hidden="true">
            <ArrowRight className="size-4" />
          </span>
          <span className="tabular text-xl font-semibold text-foreground">{sent}</span>
          отправлено за сутки
        </p>
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <span className="flex size-7 items-center justify-center rounded-full bg-status-talent-tint text-status-talent-deep" aria-hidden="true">
            <ArrowLeft className="size-4" />
          </span>
          <span className="tabular text-xl font-semibold text-foreground">{received}</span>
          принято за сутки
        </p>
      </div>
    </div>
  );
}

export function AdminIntegrationsPage(): ReactNode {
  const { hasRole } = useAuth();
  const queryClient = useQueryClient();
  // Чек-лист онбординга §7.5: no-op без провайдера.
  const { completeChecklistItem } = useOnboarding();
  const [jsonTarget, setJsonTarget] = useState<IntegrationEventRecord | null>(null);

  // Нефильтрованная выборка для карточек коннекторов.
  const overviewQuery = useQuery({
    queryKey: ['integration-events-overview'],
    queryFn: ({ signal }) => listIntegrationEvents({}, { limit: 200, signal }),
    refetchInterval: 30_000,
  });

  // Поллинг журнала 15 с: инвалидация серверного запроса DataTable.
  useEffect(() => {
    const timer = window.setInterval(() => {
      void queryClient.invalidateQueries({ queryKey: ['registry', 'admin.integrations'] });
    }, 15_000);
    return () => window.clearInterval(timer);
  }, [queryClient]);

  const retryMutation = useMutation({
    mutationFn: (eventId: string) => retryIntegrationEvent(eventId),
    meta: { silent: true },
    onSuccess: () => {
      toastSuccess('Событие поставлено на повторную доставку');
      void queryClient.invalidateQueries({ queryKey: ['registry', 'admin.integrations'] });
      void queryClient.invalidateQueries({ queryKey: ['integration-events-overview'] });
      // Чек-лист онбординга §7.5: работа с событиями интеграции.
      completeChecklistItem('test-integration');
    },
    onError: (error) => toastError(error, { title: 'Повтор не запустился' }),
  });

  /**
   * Адаптер fetcher: GET /admin/integration/events без offset — берём
   * offset+limit и режем на клиенте; поиск — по типу события на странице.
   */
  const fetcher = useMemo(
    () =>
      async (params: RegistryFetchParams): Promise<ListEnvelope<IntegrationEventRecord>> => {
        const apiFilters: IntegrationEventFilters = {
          direction: (params.filters.direction as IntegrationEventFilters['direction']) || undefined,
          system: (params.filters.system as IntegrationEventFilters['system']) || undefined,
          status: (params.filters.status as string | undefined) || undefined,
        };
        const envelope = await listIntegrationEvents(apiFilters, {
          limit: params.offset + params.limit,
          signal: params.signal,
        });
        const term = params.search?.trim().toLowerCase() ?? '';
        const items = term
          ? envelope.items.filter((e) => e.event_type.toLowerCase().includes(term))
          : envelope.items;
        return {
          items: items.slice(params.offset, params.offset + params.limit),
          total: term ? items.length : envelope.total,
          limit: params.limit,
          offset: params.offset,
        };
      },
    [],
  );

  const columns: DataTableColumn<IntegrationEventRecord>[] = useMemo(
    () => [
      {
        key: 'created_at',
        title: 'Время',
        width: 160,
        alwaysVisible: true,
        render: (_v, e) => <span className="tabular">{formatDateTime(e.created_at)}</span>,
      },
      {
        key: 'direction',
        title: 'Напр.',
        width: 70,
        render: (_v, e) => (
          <Tooltip>
            <TooltipTrigger asChild>
              <span
                className={cn(
                  'flex size-7 items-center justify-center rounded-full',
                  e.direction === 'outbound'
                    ? 'bg-primary-tint text-primary'
                    : 'bg-status-talent-tint text-status-talent-deep',
                )}
                aria-label={
                  e.direction === 'outbound' ? 'CRM → внешняя система' : 'Внешняя система → CRM'
                }
              >
                {e.direction === 'outbound' ? (
                  <ArrowRight className="size-4" aria-hidden="true" />
                ) : (
                  <ArrowLeft className="size-4" aria-hidden="true" />
                )}
              </span>
            </TooltipTrigger>
            <TooltipContent>
              {e.direction === 'outbound' ? 'CRM → внешняя система' : 'Внешняя система → CRM'}
            </TooltipContent>
          </Tooltip>
        ),
      },
      {
        key: 'system',
        title: 'Система',
        width: 90,
        render: (_v, e) => e.system.toUpperCase(),
      },
      { key: 'event_type', title: 'Тип события', dataIndex: 'event_type', ellipsis: true },
      {
        key: 'entity',
        title: 'Объект',
        render: (_v, e) =>
          e.entity_type === 'deal' && e.entity_id ? (
            <Link to={`/requests/${e.entity_id}`} className="text-primary hover:underline">
              заявка
            </Link>
          ) : e.entity_id ? (
            <span className="text-muted-foreground">{e.entity_type ?? '—'}</span>
          ) : (
            '—'
          ),
      },
      {
        key: 'status',
        title: 'Статус',
        width: 130,
        render: (_v, e) => statusBadge(e.status),
      },
      {
        key: 'attempts',
        title: 'Попытки',
        width: 90,
        align: 'right',
        responsive: ['lg'],
        render: (_v, e) =>
          e.last_error ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="tabular underline decoration-dotted">{e.attempts}</span>
              </TooltipTrigger>
              <TooltipContent className="max-w-[360px]">
                Последняя ошибка: {e.last_error}
              </TooltipContent>
            </Tooltip>
          ) : (
            <span className="tabular">{e.attempts}</span>
          ),
      },
      {
        key: 'actions',
        title: '',
        width: 100,
        render: (_v, e) => (
          <span className="flex items-center gap-1">
            <Button
              variant="outline"
              size="icon"
              className="size-7"
              aria-label="Показать JSON события"
              onClick={() => setJsonTarget(e)}
            >
              <Braces aria-hidden="true" />
            </Button>
            {hasRole('admin') && (e.status === 'dead' || e.status === 'retrying') ? (
              <Button
                variant="outline"
                size="icon"
                className="size-7"
                aria-label="Повторить доставку"
                disabled={retryMutation.isPending && retryMutation.variables === e.event_id}
                onClick={() => retryMutation.mutate(e.event_id)}
              >
                <RotateCw aria-hidden="true" />
              </Button>
            ) : null}
          </span>
        ),
      },
    ],
    [hasRole, retryMutation],
  );

  const overview = overviewQuery.data?.items ?? [];

  return (
    <>
      <PageHeader
        title="Интеграции LMS/CMS"
        subtitle="Двусторонний обмен на едином конверте событий: outbox, ретраи 1м/5м/30м/2ч/6ч, DLQ"
      />
      <StaggerGrid data-tour="admin-integrations" className="mb-4 grid grid-cols-12 gap-4">
        <StaggerItem className="col-span-12 md:col-span-6">
          <ConnectorCard system="lms" title="LMS (обучение)" events={overview} />
        </StaggerItem>
        <StaggerItem className="col-span-12 md:col-span-6">
          <ConnectorCard system="cms" title="CMS (сайт-витрина)" events={overview} />
        </StaggerItem>
      </StaggerGrid>
      <DataTable<IntegrationEventRecord>
        screen="admin.integrations"
        columns={columns}
        rowKey="id"
        fetcher={fetcher}
        searchPlaceholder="Тип события (на странице)"
        renderFilters={({ filters, setFilter }) => (
          <>
            <FilterSelect
              placeholder="Направление"
              allLabel="Направление: любое"
              options={[
                { value: 'outbound', label: '→ из CRM' },
                { value: 'inbound', label: '← в CRM' },
              ]}
              value={filters.direction as string | undefined}
              onChange={(value) => setFilter('direction', value)}
            />
            <FilterSelect
              placeholder="Система"
              allLabel="Система: любая"
              options={[
                { value: 'lms', label: 'LMS' },
                { value: 'cms', label: 'CMS' },
              ]}
              value={filters.system as string | undefined}
              onChange={(value) => setFilter('system', value)}
            />
            <FilterSelect
              placeholder="Статус"
              allLabel="Статус: любой"
              options={Object.entries(STATUS_META).map(([value, meta]) => ({
                value,
                label: meta.label,
              }))}
              value={filters.status as string | undefined}
              onChange={(value) => setFilter('status', value)}
            />
          </>
        )}
        emptyIllustration="notifications-empty"
        emptyTitle="Событий пока нет"
        emptyDescription="Обмен появится после переходов заявок с LMS-передачей или вебхуков CMS"
      />
      <Dialog
        open={Boolean(jsonTarget)}
        onOpenChange={(open) => (open ? undefined : setJsonTarget(null))}
      >
        <DialogContent className="sm:max-w-[720px]">
          <DialogHeader>
            <DialogTitle>{jsonTarget ? `Событие ${jsonTarget.event_type}` : ''}</DialogTitle>
            <DialogDescription>Тело события в формате конверта интеграции</DialogDescription>
          </DialogHeader>
          <pre className="max-h-[480px] overflow-auto rounded-md bg-sidebar p-4 text-xs text-sidebar-muted">
            {JSON.stringify(jsonTarget?.payload ?? {}, null, 2)}
          </pre>
          {jsonTarget?.last_error ? (
            <p className="text-sm text-status-danger-deep">Последняя ошибка: {jsonTarget.last_error}</p>
          ) : null}
        </DialogContent>
      </Dialog>
    </>
  );
}
