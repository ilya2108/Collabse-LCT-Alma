import {
  DndContext,
  DragOverlay,
  MeasuringStrategy,
  PointerSensor,
  TouchSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from '@dnd-kit/core';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { FileUp, Info, Plus, Search, X } from 'lucide-react';
import { LayoutGroup, motion } from 'motion/react';
import { useCallback, useMemo, useRef, useState, type ReactNode } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { PresetsControl } from '@/shared/ui/data-table';
import { CreateRequestDrawer } from '@/components/requests/CreateRequestDrawer';
import { RequestDrawer } from '@/components/requests/RequestDrawer';
import { ErrorPanel } from '@/components/requests/StatePanels';
import { TransitionCommentModal } from '@/components/requests/TransitionCommentModal';
import { UniversitySelect, ProductSelect, UserSelect } from '@/components/selects/EntitySelects';
import { listPresets } from '@/shared/api/endpoints/presets';
import { listActiveProducts } from '@/shared/api/endpoints/products';
import { useOnboarding } from '@/features/onboarding';
import { listRequests, performTransition } from '@/shared/api/endpoints/requests';
import { getWorkflow, listWorkflows } from '@/shared/api/endpoints/workflows';
import type { ListEnvelope, RequestItem, UiPreset, WorkflowType } from '@/shared/api/types';
import { useAuth } from '@/shared/auth/AuthContext';
import { springs } from '@/shared/lib/motion';
import { toastError } from '@/shared/lib/toast';
import { useDebouncedValue } from '@/shared/lib/useDebouncedValue';
import { useSseInvalidate } from '@/shared/lib/useSseInvalidate';
import { cn } from '@/shared/lib/cn';
import { Button } from '@/shared/ui/button';
import { Checkbox } from '@/shared/ui/checkbox';
import { Input } from '@/shared/ui/input';
import { Skeleton } from '@/shared/ui/skeleton';
import { BoardCard } from './BoardCard';
import { BoardColumn, type ColumnDropState } from './BoardColumn';
import { BoardEmptyState } from './BoardEmptyState';
import {
  allowedTargetsLabel,
  buildColumns,
  canDragRequest,
  transitionsFrom,
  type TransitionTarget,
} from './boardModel';

/**
 * Канбан-доска заявок (ux.md §7, redesign.md §6.4): колонки — этапы живой
 * схемы workflow, drag&drop строго по графу переходов (вкл. возвраты с
 * обязательным комментарием), optimistic update с откатом на 409, realtime по
 * SSE. Фильтры сериализуются в URL (§7.3) — ссылка шарится коллеге.
 */

const REQUESTS_LIMIT = 200;

interface PendingTransition {
  request: RequestItem;
  target: TransitionTarget;
}

/** Локальный сегмент B2B/B2C со скользящей подложкой (§3.10 SegmentedControl). */
function WorkflowSegmented({
  value,
  onChange,
}: {
  value: WorkflowType;
  onChange: (value: WorkflowType) => void;
}): ReactNode {
  const options: Array<{ value: WorkflowType; label: string }> = [
    { value: 'b2b', label: 'B2B (вузы)' },
    { value: 'b2c', label: 'B2C (физлица и юрлица)' },
  ];
  return (
    <div
      className="inline-flex h-9 items-center gap-0.5 rounded-md bg-muted p-0.5"
      role="group"
      aria-label="Процесс"
      data-tour="board-wf-switch"
    >
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={value === option.value}
          onClick={() => onChange(option.value)}
          className={cn(
            'relative h-8 rounded-[6px] px-3 text-sm font-medium transition-colors duration-150',
            value === option.value ? 'text-foreground' : 'text-muted-foreground hover:text-foreground',
          )}
        >
          {value === option.value ? (
            <motion.span
              layoutId="board-wf-thumb"
              transition={springs.snappy}
              className="absolute inset-0 rounded-[6px] bg-card shadow-card"
              aria-hidden="true"
            />
          ) : null}
          <span className="relative">{option.label}</span>
        </button>
      ))}
    </div>
  );
}

/** Скелетон доски — повторяет каркас колонок (§2.3). */
function BoardSkeleton(): ReactNode {
  return (
    <div className="flex items-stretch gap-3 overflow-hidden pb-2" aria-hidden="true">
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="w-[300px] min-w-[300px] rounded-lg bg-muted/60 p-3">
          <Skeleton className="mb-3 h-4 w-2/3" />
          {Array.from({ length: 3 - (i % 2) }).map((_, j) => (
            <Skeleton key={j} className="mb-2 h-20" />
          ))}
        </div>
      ))}
    </div>
  );
}

export function BoardPage(): ReactNode {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { user, roles, hasRole, hasPermission } = useAuth();
  // Чек-лист онбординга §7.5: no-op без провайдера.
  const { completeChecklistItem } = useOnboarding();
  const [searchParams, setSearchParams] = useSearchParams();

  // --- Фильтры из URL (§7.3) -------------------------------------------------
  const wfType: WorkflowType = searchParams.get('wf') === 'b2c' ? 'b2c' : 'b2b';
  const filterKam = searchParams.get('kam') ?? undefined;
  const filterUniversity = searchParams.get('university') ?? undefined;
  const filterProduct = searchParams.get('product') ?? undefined;
  const filterStuck = searchParams.get('stuck') === 'true';
  const filterStatus = searchParams.get('status') ?? undefined;
  const [search, setSearch] = useState(searchParams.get('search') ?? '');
  const debouncedSearch = useDebouncedValue(search, 400);

  const setParam = useCallback(
    (key: string, value: string | undefined) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (value === undefined || value === '') next.delete(key);
          else next.set(key, value);
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  // --- Данные -----------------------------------------------------------------
  const workflowsQuery = useQuery({
    queryKey: ['workflows'],
    queryFn: ({ signal }) => listWorkflows(signal),
    staleTime: 60_000,
  });
  const workflowId = workflowsQuery.data?.find((w) => w.type === wfType)?.id;

  const schemaQuery = useQuery({
    queryKey: ['workflow-schema', workflowId],
    queryFn: ({ signal }) => getWorkflow(workflowId as string, signal),
    enabled: Boolean(workflowId),
    staleTime: 60_000,
  });
  const schema = schemaQuery.data;

  /** Фильтр `status` из URL может быть кодом этапа (drill-down с дашборда). */
  const statusIdFilter = useMemo(() => {
    if (!filterStatus || !schema) return undefined;
    const byId = schema.statuses.find((s) => s.id === filterStatus);
    const byCode = schema.statuses.find((s) => s.code === filterStatus);
    return (byId ?? byCode)?.id;
  }, [filterStatus, schema]);

  const requestsKey = useMemo(
    () => [
      'board-requests',
      wfType,
      {
        kam: filterKam ?? '',
        university: filterUniversity ?? '',
        product: filterProduct ?? '',
        stuck: filterStuck,
        status: statusIdFilter ?? '',
        search: debouncedSearch.trim(),
      },
    ],
    [wfType, filterKam, filterUniversity, filterProduct, filterStuck, statusIdFilter, debouncedSearch],
  );

  const requestsQuery = useQuery({
    queryKey: requestsKey,
    queryFn: ({ signal }) =>
      listRequests(
        {
          workflow_type: wfType,
          assignee_id: filterKam,
          university_id: filterUniversity,
          product_id: filterProduct,
          stuck: filterStuck || undefined,
          status_id: statusIdFilter ? [statusIdFilter] : undefined,
          search: debouncedSearch.trim() || undefined,
        },
        { limit: REQUESTS_LIMIT, sort: '-updated_at', signal },
      ),
    enabled: Boolean(workflowId),
    // смена фильтров не «моргает» доской — данные остаются до прихода новых
    placeholderData: keepPreviousData,
  });

  const productsQuery = useQuery({
    queryKey: ['products-select'],
    queryFn: ({ signal }) => listActiveProducts(signal),
    staleTime: 60_000,
  });
  const productNames = useMemo(() => {
    const map = new Map<string, string>();
    for (const product of productsQuery.data?.items ?? []) map.set(product.id, product.name);
    return map;
  }, [productsQuery.data]);

  // --- Realtime (SSE) ----------------------------------------------------------
  const [schemaChangedBanner, setSchemaChangedBanner] = useState(false);
  useSseInvalidate('request.transitioned', [['board-requests']]);
  const workflowKeys = useMemo(() => [['workflow-schema'], ['workflows'], ['board-requests']], []);
  useSseInvalidate('workflow.changed', workflowKeys, () => {
    setSchemaChangedBanner(true);
    return true;
  });

  // --- Пресеты (screen: "board", ux.md §7.1) -----------------------------------
  const presetsQuery = useQuery({
    queryKey: ['ui-presets', 'board'],
    queryFn: ({ signal }) => listPresets('board', signal),
    staleTime: 60_000,
    retry: 0,
  });
  const [activePresetId, setActivePresetId] = useState<string | null>(null);
  const applyPreset = useCallback(
    (preset: UiPreset) => {
      const filters = (preset.state.filters ?? {}) as Record<string, unknown>;
      setSearchParams(
        () => {
          const next = new URLSearchParams();
          for (const key of ['wf', 'kam', 'university', 'product', 'stuck', 'status', 'search']) {
            const value = filters[key];
            if (typeof value === 'string' && value) next.set(key, value);
            if (value === true) next.set(key, 'true');
          }
          return next;
        },
        { replace: true },
      );
      setSearch(typeof filters.search === 'string' ? filters.search : '');
      setActivePresetId(preset.id);
    },
    [setSearchParams],
  );

  // --- Drag&drop ----------------------------------------------------------------
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 250, tolerance: 8 } }),
  );
  const [activeRequest, setActiveRequest] = useState<RequestItem | null>(null);
  const [pending, setPending] = useState<PendingTransition | null>(null);
  /** Заявка, только что переведённая дропом, — пульс фона primary-tint (§2.3). */
  const [pulseRequestId, setPulseRequestId] = useState<string | null>(null);
  const pulseTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const allowedTargetsRef = useRef<TransitionTarget[]>([]);
  /** После дропа браузер добивает click по карточке — гасим открытие Sheet'а. */
  const lastDragEndRef = useRef(0);

  const allowedTargets = activeRequest && schema ? allowedTargetsRef.current : [];

  const startPulse = useCallback((requestId: string) => {
    if (pulseTimerRef.current) clearTimeout(pulseTimerRef.current);
    setPulseRequestId(requestId);
    pulseTimerRef.current = setTimeout(() => setPulseRequestId(null), 600);
  }, []);

  const transitionMutation = useMutation({
    mutationFn: ({ request, target, comment }: PendingTransition & { comment?: string }) =>
      performTransition(request.id, {
        to_status_id: target.toStatus.id,
        comment,
        version: request.version,
      }),
    meta: { silent: true },
    onMutate: ({ request, target }) => {
      // optimistic: карточка сразу встаёт в целевую колонку (ux.md §7.2)
      const previous = queryClient.getQueryData<ListEnvelope<RequestItem>>(requestsKey);
      queryClient.setQueryData<ListEnvelope<RequestItem>>(requestsKey, (old) =>
        old
          ? {
              ...old,
              items: old.items.map((item) =>
                item.id === request.id
                  ? {
                      ...item,
                      status: {
                        id: target.toStatus.id,
                        code: target.toStatus.code,
                        name: target.toStatus.name,
                        color: target.toStatus.color,
                      },
                      status_updated_at: new Date().toISOString(),
                      is_stuck: false,
                      stuck_days: 0,
                    }
                  : item,
              ),
            }
          : old,
      );
      startPulse(request.id);
      return { previous };
    },
    onError: (error, _variables, context) => {
      if (context?.previous) queryClient.setQueryData(requestsKey, context.previous);
      toastError(error, { title: 'Переход не выполнен' });
      // 409: схему могли поменять или заявку уже перевели — перечитываем
      void queryClient.invalidateQueries({ queryKey: ['board-requests'] });
      void queryClient.invalidateQueries({ queryKey: ['workflow-schema'] });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['board-requests'] });
      void queryClient.invalidateQueries({ queryKey: ['request'] });
      completeChecklistItem('move-request');
    },
    onSettled: () => setPending(null),
  });

  const startTransition = (request: RequestItem, target: TransitionTarget): void => {
    if (target.transition.requires_comment || target.transition.kind === 'return') {
      setPending({ request, target });
    } else {
      transitionMutation.mutate({ request, target });
    }
  };

  const handleDragStart = ({ active }: DragStartEvent): void => {
    const request = (active.data.current as { request?: RequestItem } | undefined)?.request;
    if (!request || !schema) return;
    allowedTargetsRef.current = transitionsFrom(schema, request.status.id, roles);
    setActiveRequest(request);
  };

  const handleDragEnd = ({ over }: DragEndEvent): void => {
    const request = activeRequest;
    const targets = allowedTargetsRef.current;
    setActiveRequest(null);
    lastDragEndRef.current = Date.now();
    if (!request || !over || !schema) return;
    const toStatusId = String(over.id);
    if (toStatusId === request.status.id) return;
    const target = targets.find((t) => t.toStatus.id === toStatusId);
    if (!target) {
      const toStatus = schema.statuses.find((s) => s.id === toStatusId);
      toastError(
        new Error(
          `Переход «${request.status.name}» → «${toStatus?.name ?? '?'}» не предусмотрен схемой. ` +
            `Разрешены: ${allowedTargetsLabel(targets)}.`,
        ),
        { title: 'Переход не разрешён' },
      );
      return;
    }
    startTransition(request, target);
  };

  // --- Колонки -------------------------------------------------------------------
  const [collapsedOverrides, setCollapsedOverrides] = useState<Record<string, boolean>>({});
  const toggleCollapsed = useCallback((statusId: string) => {
    setCollapsedOverrides((prev) => ({ ...prev, [statusId]: !(prev[statusId] ?? true) }));
  }, []);

  const requests = requestsQuery.data?.items ?? [];
  const columns = schema ? buildColumns(schema, requests) : [];
  const boardEmpty = requests.length === 0 && !requestsQuery.isLoading && !requestsQuery.isError;

  const [createOpen, setCreateOpen] = useState(false);
  const [openedRequestId, setOpenedRequestId] = useState<string | null>(null);

  const dropStateFor = (statusId: string): ColumnDropState => {
    if (!activeRequest) return 'idle';
    if (statusId === activeRequest.status.id) return 'source';
    return allowedTargets.some((t) => t.toStatus.id === statusId) ? 'allowed' : 'forbidden';
  };

  const canDrag = (request: RequestItem): boolean =>
    hasPermission('requests:transition') && canDragRequest(request, roles, user?.id);
  const dragHint = (request: RequestItem): string | undefined =>
    hasRole('kam') && !hasRole('admin', 'head_kam') && request.assignee && request.assignee.id !== user?.id
      ? `Заявка КАМа: ${request.assignee.full_name}`
      : undefined;

  // --- Рендер ----------------------------------------------------------------------
  let body: ReactNode;
  if (schemaQuery.isError || workflowsQuery.isError) {
    body = (
      <ErrorPanel
        error={schemaQuery.error ?? workflowsQuery.error}
        title="Не удалось загрузить схему процесса"
        onRetry={() => {
          void workflowsQuery.refetch();
          void schemaQuery.refetch();
        }}
      />
    );
  } else if (!schema || (requestsQuery.isLoading && requests.length === 0)) {
    body = <BoardSkeleton />;
  } else if (requestsQuery.isError) {
    body = <ErrorPanel error={requestsQuery.error} onRetry={() => void requestsQuery.refetch()} />;
  } else {
    body = (
      <DndContext
        sensors={sensors}
        onDragStart={handleDragStart}
        onDragEnd={handleDragEnd}
        // терминальные «стопки» разворачиваются при захвате карточки —
        // границы droppable-колонок перемеряются постоянно
        measuring={{ droppable: { strategy: MeasuringStrategy.Always } }}
      >
        <LayoutGroup>
          <div
            key={wfType}
            className="flex min-h-[calc(100vh-320px)] snap-x snap-proximity items-stretch gap-3 overflow-x-auto pb-2"
          >
            {columns.map((column, index) => (
              <BoardColumn
                key={column.status.id}
                column={column}
                index={index}
                dropState={dropStateFor(column.status.id)}
                collapsed={
                  collapsedOverrides[column.status.id] ?? (column.status.is_terminal && !activeRequest)
                }
                onToggleCollapsed={toggleCollapsed}
                canDrag={canDrag}
                dragHint={dragHint}
                pulseRequestId={pulseRequestId}
                productName={(request) =>
                  request.product_id ? productNames.get(request.product_id) : undefined
                }
                onOpen={(request) => {
                  if (Date.now() - lastDragEndRef.current > 250) setOpenedRequestId(request.id);
                }}
                emptyContent={
                  index === 0 && boardEmpty ? (
                    <BoardEmptyState
                      title="Заявок нет"
                      description="Создайте первую заявку или импортируйте данные из Excel"
                      actions={
                        <>
                          {hasPermission('requests:write') ? (
                            <Button onClick={() => setCreateOpen(true)}>
                              <Plus aria-hidden="true" />
                              Создать заявку
                            </Button>
                          ) : null}
                          {hasPermission('import:run') ? (
                            <Button variant="outline" onClick={() => navigate('/import')}>
                              <FileUp aria-hidden="true" />
                              Импортировать
                            </Button>
                          ) : null}
                        </>
                      }
                    />
                  ) : undefined
                }
              />
            ))}
          </div>
        </LayoutGroup>
        <DragOverlay dropAnimation={null}>
          {activeRequest ? (
            <div style={{ width: 276 }}>
              <BoardCard
                overlay
                request={activeRequest}
                draggable={false}
                productName={
                  activeRequest.product_id ? productNames.get(activeRequest.product_id) : undefined
                }
                onOpen={() => undefined}
              />
            </div>
          ) : null}
        </DragOverlay>
      </DndContext>
    );
  }

  const total = requestsQuery.data?.total ?? 0;

  return (
    <>
      {/* заголовок экрана (§3.3 PageHeader-паттерн) */}
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <h1 className="text-xl font-semibold leading-7 text-balance">Доска заявок</h1>
        {hasPermission('requests:write') ? (
          <Button onClick={() => setCreateOpen(true)}>
            <Plus aria-hidden="true" />
            Создать заявку
          </Button>
        ) : null}
      </div>

      {schemaChangedBanner ? (
        <div
          role="status"
          className="mb-3 flex items-center gap-2 rounded-md border border-primary-tint-2 bg-primary-tint px-3 py-2 text-sm text-foreground"
        >
          <Info className="size-4 shrink-0 text-primary" aria-hidden="true" />
          <span className="flex-1">Схема процесса обновлена администратором — доска перестроена</span>
          <Button
            variant="ghost"
            size="icon"
            className="size-7"
            aria-label="Скрыть уведомление"
            onClick={() => setSchemaChangedBanner(false)}
          >
            <X aria-hidden="true" />
          </Button>
        </div>
      ) : null}

      {/* панель фильтров */}
      <div
        data-tour="board-filters"
        className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border bg-card p-3 shadow-card"
      >
        <WorkflowSegmented
          value={wfType}
          onChange={(value) => {
            // одним вызовом: два последовательных setSearchParams в одном клике
            // считаются от одних и тех же (устаревших) параметров рендера —
            // второй затирал wf=b2c, и переключение B2B→B2C «не работало»
            setSearchParams(
              (prev) => {
                const next = new URLSearchParams(prev);
                next.set('wf', value);
                next.delete('status');
                return next;
              },
              { replace: true },
            );
          }}
        />
        <PresetsControl
          screen="board"
          presets={presetsQuery.data?.items ?? []}
          presetsAvailable={!presetsQuery.isError}
          activePresetId={activePresetId}
          currentState={() => ({
            filters: {
              wf: wfType,
              kam: filterKam,
              university: filterUniversity,
              product: filterProduct,
              stuck: filterStuck || undefined,
              search: debouncedSearch.trim() || undefined,
            },
          })}
          onApply={applyPreset}
          onClear={() => setActivePresetId(null)}
        />
        <div className="relative">
          <Search
            className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden="true"
          />
          <Input
            type="search"
            placeholder="Поиск по заявкам…"
            aria-label="Поиск по заявкам"
            className="w-52 pl-8"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setParam('search', e.target.value || undefined);
            }}
          />
        </div>
        {hasRole('admin', 'head_kam') ? (
          <UserSelect
            style={{ width: 190 }}
            placeholder="КАМ"
            value={filterKam}
            onChange={(value) => setParam('kam', value)}
          />
        ) : hasRole('kam') ? (
          <label className="flex min-h-9 cursor-pointer items-center gap-2 text-sm">
            <Checkbox
              checked={filterKam === user?.id}
              onCheckedChange={(checked) => setParam('kam', checked === true ? user?.id : undefined)}
            />
            Только мои
          </label>
        ) : null}
        {wfType === 'b2b' ? (
          <UniversitySelect
            style={{ width: 210 }}
            value={filterUniversity}
            onChange={(value) => setParam('university', value)}
          />
        ) : null}
        <ProductSelect
          style={{ width: 190 }}
          value={filterProduct}
          onChange={(value) => setParam('product', value)}
        />
        <label className="flex min-h-9 cursor-pointer items-center gap-2 text-sm">
          <Checkbox
            checked={filterStuck}
            onCheckedChange={(checked) => setParam('stuck', checked === true ? 'true' : undefined)}
          />
          Только зависшие
        </label>
        {statusIdFilter ? (
          <Button variant="link" size="sm" onClick={() => setParam('status', undefined)}>
            Сбросить фильтр этапа
          </Button>
        ) : null}
        {total > REQUESTS_LIMIT ? (
          <span className="ml-auto text-xs text-status-warning-deep">
            Показаны первые {REQUESTS_LIMIT} из {total} — уточните фильтры
          </span>
        ) : null}
      </div>

      {body}

      <CreateRequestDrawer
        workflowType={wfType}
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(request) => setOpenedRequestId(request.id)}
      />
      <RequestDrawer
        requestId={openedRequestId}
        onClose={() => setOpenedRequestId(null)}
      />
      {pending ? (
        <TransitionCommentModal
          open
          actionName={pending.target.transition.name}
          toStatusName={pending.target.toStatus.name}
          isReturn={pending.target.transition.kind === 'return'}
          loading={transitionMutation.isPending}
          onConfirm={(comment) => transitionMutation.mutate({ ...pending, comment })}
          onCancel={() => setPending(null)}
        />
      ) : null}
    </>
  );
}
