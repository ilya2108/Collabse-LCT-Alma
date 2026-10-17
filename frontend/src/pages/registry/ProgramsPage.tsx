import {
  DndContext,
  PointerSensor,
  TouchSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core';
import {
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
  arrayMove,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowDown,
  ArrowUp,
  ChevronsUpDown,
  GripVertical,
  ListOrdered,
  TrendingUp,
} from 'lucide-react';
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
  type HTMLAttributes,
  type ReactNode,
} from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { listAllItDirections } from '@/shared/api/endpoints/itDirections';
import { listPrograms, reorderProgramPriorities } from '@/shared/api/endpoints/programs';
import { listActiveProducts } from '@/shared/api/endpoints/products';
import { getProgramsDemand, type ProgramDemandItem } from '@/shared/api/endpoints/reports';
import type { Program } from '@/shared/api/types';
import { useAuth } from '@/shared/auth/AuthContext';
import { cn } from '@/shared/lib/cn';
import { dayjs } from '@/shared/lib/dayjs';
import { useDebouncedValue } from '@/shared/lib/useDebouncedValue';
import { formatDate, formatText } from '@/shared/lib/format';
import { toastError } from '@/shared/lib/toast';
import { Badge } from '@/shared/ui/badge';
import { Button } from '@/shared/ui/button';
import {
  EmptyState,
  ErrorState,
  ExportMenu,
  FilterSelect,
  LoadingState,
  PageHeader,
  ProductCombobox,
  SegmentedControl,
  StatusBadge,
  UniversityCombobox,
} from '@/shared/ui/data-table';
import { Input } from '@/shared/ui/input';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/ui/table';

/**
 * Реестр программ с ручным ранжированием (ux.md §9.4, redesign.md §6.5):
 * `priority_rank` — меньше = выше; drag строки (GripVertical) или ▲/▼ вызывают
 * POST /programs/priorities/reorder (optimistic). Тумблер «Ручной приоритет /
 * Востребованность» — SegmentedControl (§3.10); `?mode=demand` открывает второй режим
 * (переход с дашборда).
 *
 * «Востребованность» (§2 ТЗ): три показателя по каждой программе — заявки на обучение,
 * обучающиеся, параллельные потоки — за выбранный период, ручной приоритет рядом;
 * при фичефлаге auto_ranking — расчётный индекс 0–100 (справочно, приоритет не меняет).
 */

const RANK_STEP = 10;

// --- Drag&drop строка таблицы -----------------------------------------------

interface RowContextValue {
  setActivatorNodeRef?: (element: HTMLElement | null) => void;
  listeners?: Record<string, unknown>;
}

const RowContext = createContext<RowContextValue>({});

function DragHandle(): ReactNode {
  const { setActivatorNodeRef, listeners } = useContext(RowContext);
  return (
    <button
      type="button"
      ref={setActivatorNodeRef}
      aria-label="Перетащить строку"
      className="inline-flex cursor-grab touch-none items-center rounded-sm px-1 py-0.5 text-muted-foreground hover:text-foreground active:cursor-grabbing"
      {...(listeners as HTMLAttributes<HTMLButtonElement> | undefined)}
    >
      <GripVertical className="size-4" aria-hidden="true" />
    </button>
  );
}

interface SortableRowProps extends HTMLAttributes<HTMLTableRowElement> {
  rowId: string;
  children: ReactNode;
}

function SortableRow({ rowId, children, ...props }: SortableRowProps): ReactNode {
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } =
    useSortable({ id: rowId });
  const style: CSSProperties = {
    transform: CSS.Translate.toString(transform),
    transition,
  };
  const context = useMemo<RowContextValue>(
    () => ({ setActivatorNodeRef, listeners: listeners as Record<string, unknown> | undefined }),
    [setActivatorNodeRef, listeners],
  );
  return (
    <RowContext.Provider value={context}>
      <TableRow
        ref={setNodeRef}
        style={style}
        className={isDragging ? 'relative z-10 bg-primary-tint shadow-drag' : undefined}
        {...attributes}
        {...props}
      >
        {children}
      </TableRow>
    </RowContext.Provider>
  );
}

// --- Режим «Востребованность» (отчёт programs-demand, §7.2) ---------------------

type DemandPeriod = 'all' | '30' | '90' | '365';

const DEMAND_PERIODS: { value: DemandPeriod; label: string }[] = [
  { value: 'all', label: 'Всё время' },
  { value: '30', label: '30 дней' },
  { value: '90', label: 'Квартал' },
  { value: '365', label: 'Год' },
];

/** Период пресета: последние N дней включительно по сегодняшний. */
function demandPeriodRange(period: DemandPeriod): { from?: string; to?: string } {
  if (period === 'all') return {};
  const to = dayjs();
  return {
    from: to.subtract(Number(period) - 1, 'day').format('YYYY-MM-DD'),
    to: to.format('YYYY-MM-DD'),
  };
}

type DemandSortKey =
  | 'requests_count'
  | 'students_studying'
  | 'parallel_streams'
  | 'priority_rank'
  | 'computed_demand';

interface DemandColumn {
  key: DemandSortKey;
  label: string;
  hint: string;
}

const DEMAND_COLUMNS: DemandColumn[] = [
  {
    key: 'requests_count',
    label: 'Заявки на обучение',
    hint: 'Заявки по программе, созданные в периоде (без архива); ниже — сколько из них открыто',
  },
  {
    key: 'students_studying',
    label: 'Обучающиеся',
    hint: 'Студенты программы в статусе «Обучается» — сейчас или хотя бы день периода',
  },
  {
    key: 'parallel_streams',
    label: 'Параллельные потоки',
    hint: 'Заявки программы на этапе обучения (сейчас или в периоде); два потока в одном вузе — два',
  },
  { key: 'priority_rank', label: 'Ручной приоритет', hint: 'Меньше = выше; меняется в режиме «Ручной приоритет»' },
];

const INDEX_COLUMN: DemandColumn = {
  key: 'computed_demand',
  label: 'Расчётный спрос',
  hint: 'Индекс 0–100: среднее долей от максимума по трём показателям',
};

function demandValue(row: ProgramDemandItem, key: DemandSortKey): number {
  return key === 'computed_demand' ? (row.computed_demand ?? 0) : row[key];
}

/**
 * ИТ-направление программы в строке реестра: своё — бейджем, без своего — направления её
 * продукта приглушённо (так же программу относят к направлению фильтры и отчёты).
 */
function ProgramDirectionCell({ program }: { program: Program }): ReactNode {
  if (program.direction) return <Badge variant="secondary">{program.direction.name}</Badge>;
  const inherited = program.product_directions ?? [];
  if (inherited.length === 0) return formatText(null);
  const names = inherited.map((d) => d.name).join(', ');
  return (
    <span className="line-clamp-2 text-xs text-muted-foreground" title={`По продукту: ${names}`}>
      по продукту: {names}
    </span>
  );
}

function DemandView({
  productId,
  universityId,
  directionId,
  period,
}: {
  productId?: string;
  universityId?: string;
  directionId?: string;
  period: DemandPeriod;
}): ReactNode {
  const range = demandPeriodRange(period);
  const query = useQuery({
    queryKey: [
      'programs-demand',
      productId ?? 'all',
      universityId ?? 'all',
      directionId ?? 'all',
      range.from ?? '',
      range.to ?? '',
    ],
    queryFn: ({ signal }) =>
      getProgramsDemand(
        {
          ...range,
          product_id: productId ? [productId] : undefined,
          university_id: universityId ? [universityId] : undefined,
          direction_id: directionId ? [directionId] : undefined,
        },
        signal,
      ),
  });
  const autoRanking = query.data?.auto_ranking ?? false;
  const [sortKey, setSortKey] = useState<DemandSortKey | null>(null);
  const activeSort: DemandSortKey = sortKey ?? (autoRanking ? 'computed_demand' : 'requests_count');
  const columns = autoRanking ? [...DEMAND_COLUMNS, INDEX_COLUMN] : DEMAND_COLUMNS;
  const rows = useMemo(() => {
    const list = [...(query.data?.rows ?? [])];
    return list.sort((a, b) =>
      activeSort === 'priority_rank'
        ? a.priority_rank - b.priority_rank || a.name.localeCompare(b.name)
        : demandValue(b, activeSort) - demandValue(a, activeSort) ||
          b.requests_count - a.requests_count ||
          a.priority_rank - b.priority_rank,
    );
  }, [query.data, activeSort]);

  if (query.isLoading) return <LoadingState rows={6} card={false} />;
  if (query.isError) {
    return (
      <ErrorState
        error={query.error}
        title="Не удалось загрузить востребованность программ"
        onRetry={() => void query.refetch()}
      />
    );
  }
  if (rows.length === 0) {
    return <EmptyState illustration="search-empty" title="Нет активных программ под фильтры" compact />;
  }
  return (
    <div className="flex flex-col gap-3">
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead>Программа</TableHead>
            {columns.map((column) => {
              const active = column.key === activeSort;
              return (
                <TableHead
                  key={column.key}
                  className={cn('text-right', column.key !== 'requests_count' && 'max-md:hidden')}
                  aria-sort={active ? (column.key === 'priority_rank' ? 'ascending' : 'descending') : 'none'}
                >
                  <button
                    type="button"
                    title={column.hint}
                    onClick={() => setSortKey(column.key)}
                    className={cn(
                      'inline-flex items-center gap-1 rounded-sm font-medium hover:text-foreground',
                      active ? 'text-foreground' : 'text-muted-foreground',
                    )}
                  >
                    {column.label}
                    {active ? (
                      column.key === 'priority_rank' ? (
                        <ArrowUp className="size-3.5" aria-hidden="true" />
                      ) : (
                        <ArrowDown className="size-3.5" aria-hidden="true" />
                      )
                    ) : (
                      <ChevronsUpDown className="size-3.5 opacity-50" aria-hidden="true" />
                    )}
                  </button>
                </TableHead>
              );
            })}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.program_id}>
              <TableCell>
                <Link
                  to={`/registry/programs/${row.program_id}`}
                  className="font-medium text-primary hover:underline"
                >
                  {row.name}
                </Link>
              </TableCell>
              <TableCell className="tabular text-right">
                <span className="font-medium">{row.requests_count}</span>
                <span className="block text-xs text-muted-foreground">открыто {row.open_requests}</span>
              </TableCell>
              <TableCell className="tabular text-right font-medium max-md:hidden">
                {row.students_studying}
              </TableCell>
              <TableCell className="tabular text-right font-medium max-md:hidden">
                {row.parallel_streams}
              </TableCell>
              <TableCell className="tabular text-right text-muted-foreground max-md:hidden">
                {row.priority_rank}
              </TableCell>
              {autoRanking ? (
                <TableCell className="tabular text-right font-medium max-md:hidden">
                  {row.computed_demand ?? '—'}
                </TableCell>
              ) : null}
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <p className="text-pretty text-xs text-muted-foreground">
        Как считается: заявки на обучение — созданные{' '}
        {period === 'all' ? 'за всё время' : 'в выбранном периоде'}; обучающиеся — студенты
        программы в статусе «Обучается»; параллельные потоки — заявки программы на этапе
        обучения ({period === 'all' ? 'на текущую дату' : 'хотя бы день периода'}).{' '}
        {autoRanking
          ? `${query.data?.formula ?? ''}. Ручной приоритет индекс не меняет.`
          : 'Ранжирование — ручным приоритетом; расчётный индекс включается фичефлагом «Расчётный спрос программ».'}
      </p>
    </div>
  );
}

// --- Основной экран -----------------------------------------------------------

export function ProgramsPage(): ReactNode {
  const { hasPermission } = useAuth();
  const queryClient = useQueryClient();
  const canRank = hasPermission('programs:priority');

  const [searchParams, setSearchParams] = useSearchParams();
  const mode: 'manual' | 'demand' = searchParams.get('mode') === 'demand' ? 'demand' : 'manual';
  const setMode = (next: 'manual' | 'demand'): void => {
    setSearchParams(
      (prev) => {
        const params = new URLSearchParams(prev);
        if (next === 'demand') params.set('mode', 'demand');
        else params.delete('mode');
        return params;
      },
      { replace: true },
    );
  };
  const [demandPeriod, setDemandPeriod] = useState<DemandPeriod>('all');
  const [search, setSearch] = useState('');
  const [productId, setProductId] = useState<string | undefined>(undefined);
  const [universityId, setUniversityId] = useState<string | undefined>(undefined);
  const [directionId, setDirectionId] = useState<string | undefined>(undefined);
  const debouncedSearch = useDebouncedValue(search, 400);

  const programsQuery = useQuery({
    queryKey: [
      'programs-ranked',
      debouncedSearch,
      productId ?? '',
      universityId ?? '',
      directionId ?? '',
    ],
    queryFn: ({ signal }) =>
      listPrograms({
        limit: 200,
        offset: 0,
        sort: 'priority_rank',
        search: debouncedSearch.trim() || undefined,
        filters: { product_id: productId, university_id: universityId, direction_id: directionId },
        signal,
      }),
  });
  const directionsQuery = useQuery({
    queryKey: ['it-directions', 'all'],
    queryFn: ({ signal }) => listAllItDirections(signal),
    staleTime: 5 * 60_000,
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

  // Локальный порядок для optimistic drag&drop.
  const [ordered, setOrdered] = useState<Program[]>([]);
  useEffect(() => {
    setOrdered(programsQuery.data?.items ?? []);
  }, [programsQuery.data]);

  const reorderMutation = useMutation({
    mutationFn: reorderProgramPriorities,
    meta: { silent: true },
    onError: (error) => {
      toastError(error, { title: 'Не удалось изменить приоритет' });
      void queryClient.invalidateQueries({ queryKey: ['programs-ranked'] });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['programs-ranked'] });
    },
  });

  /** Пересчитать ранги 10, 20, 30… и отправить одним пакетом. */
  const applyOrder = (next: Program[]): void => {
    const items = next.map((program, index) => ({
      id: program.id,
      priority_rank: (index + 1) * RANK_STEP,
    }));
    setOrdered(next.map((program, index) => ({ ...program, priority_rank: (index + 1) * RANK_STEP })));
    reorderMutation.mutate(items);
  };

  const handleDragEnd = ({ active, over }: DragEndEvent): void => {
    if (!over || active.id === over.id) return;
    const from = ordered.findIndex((p) => p.id === active.id);
    const to = ordered.findIndex((p) => p.id === over.id);
    if (from === -1 || to === -1) return;
    applyOrder(arrayMove(ordered, from, to));
  };

  const move = (index: number, delta: -1 | 1): void => {
    const target = index + delta;
    if (target < 0 || target >= ordered.length) return;
    applyOrder(arrayMove(ordered, index, target));
  };

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 250, tolerance: 6 } }),
  );

  const renderRowCells = (record: Program, index: number): ReactNode => (
    <>
      <TableCell className="w-[170px]">
        <span className="flex items-center gap-1">
          {canRank ? <DragHandle /> : null}
          <span className="tabular inline-flex size-7 items-center justify-center rounded-full bg-primary-tint text-xs font-semibold text-primary">
            {record.priority_rank}
          </span>
          {canRank ? (
            <>
              <Button
                variant="ghost"
                size="icon"
                className="size-7"
                disabled={index === 0 || reorderMutation.isPending}
                onClick={() => move(index, -1)}
                aria-label="Поднять приоритет"
              >
                <ArrowUp aria-hidden="true" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="size-7"
                disabled={index === ordered.length - 1 || reorderMutation.isPending}
                onClick={() => move(index, 1)}
                aria-label="Опустить приоритет"
              >
                <ArrowDown aria-hidden="true" />
              </Button>
            </>
          ) : null}
        </span>
      </TableCell>
      <TableCell>
        <Link
          to={`/registry/programs/${record.id}`}
          className="font-medium text-primary hover:underline"
        >
          {record.name}
        </Link>
      </TableCell>
      <TableCell>
        {record.product_id ? (
          <Badge variant="secondary">{productNames.get(record.product_id) ?? '…'}</Badge>
        ) : (
          formatText(null)
        )}
      </TableCell>
      <TableCell className="max-md:hidden">
        <ProgramDirectionCell program={record} />
      </TableCell>
      <TableCell className="max-lg:hidden">{formatDate(record.starts_on)}</TableCell>
      <TableCell className="tabular w-[80px] text-right max-lg:hidden">{record.seats ?? '—'}</TableCell>
      <TableCell className="max-lg:hidden">
        {record.published_to_cms ? <StatusBadge status="progress">опубликована</StatusBadge> : '—'}
      </TableCell>
    </>
  );

  const tableHead = (
    <TableHeader>
      <TableRow className="hover:bg-transparent">
        <TableHead className="w-[170px]">Приоритет</TableHead>
        <TableHead>Название</TableHead>
        <TableHead>Продукт</TableHead>
        <TableHead className="max-md:hidden">ИТ-направление</TableHead>
        <TableHead className="max-lg:hidden">Старт</TableHead>
        <TableHead className="w-[80px] text-right max-lg:hidden">Мест</TableHead>
        <TableHead className="max-lg:hidden">На сайте</TableHead>
      </TableRow>
    </TableHeader>
  );

  let manualBody: ReactNode;
  if (programsQuery.isLoading) {
    manualBody = <LoadingState rows={8} card={false} />;
  } else if (programsQuery.isError) {
    manualBody = (
      <ErrorState error={programsQuery.error} onRetry={() => void programsQuery.refetch()} />
    );
  } else if (ordered.length === 0) {
    manualBody = (
      <EmptyState
        illustration="search-empty"
        title="Программ не найдено"
        description="Измените фильтры или импортируйте программы из Excel"
      />
    );
  } else if (canRank) {
    manualBody = (
      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
        <SortableContext items={ordered.map((p) => p.id)} strategy={verticalListSortingStrategy}>
          <Table>
            {tableHead}
            <TableBody>
              {ordered.map((record, index) => (
                <SortableRow key={record.id} rowId={record.id}>
                  {renderRowCells(record, index)}
                </SortableRow>
              ))}
            </TableBody>
          </Table>
        </SortableContext>
      </DndContext>
    );
  } else {
    manualBody = (
      <Table>
        {tableHead}
        <TableBody>
          {ordered.map((record, index) => (
            <TableRow key={record.id}>{renderRowCells(record, index)}</TableRow>
          ))}
        </TableBody>
      </Table>
    );
  }

  return (
    <>
      <PageHeader
        title="Программы"
        subtitle="Ручное ранжирование по востребованности: меньше ранг — выше приоритет"
      />
      <div className="rounded-lg border bg-card p-5 shadow-card">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <SegmentedControl<'manual' | 'demand'>
              value={mode}
              onChange={setMode}
              options={[
                { value: 'manual', label: 'Ручной приоритет', icon: ListOrdered },
                { value: 'demand', label: 'Востребованность', icon: TrendingUp },
              ]}
            />
            {mode === 'demand' ? (
              <SegmentedControl<DemandPeriod>
                size="sm"
                value={demandPeriod}
                onChange={setDemandPeriod}
                options={DEMAND_PERIODS}
              />
            ) : null}
            {mode === 'manual' ? (
              <Input
                placeholder="Название программы"
                aria-label="Поиск по названию программы"
                className="w-[220px]"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            ) : null}
            <ProductCombobox
              className="w-[200px]"
              value={productId}
              onChange={(value) => setProductId(value)}
            />
            <UniversityCombobox
              className="w-[220px]"
              value={universityId}
              onChange={(value) => setUniversityId(value)}
            />
            <FilterSelect
              className="w-[200px]"
              placeholder="ИТ-направление"
              allLabel="ИТ-направление: все"
              options={(directionsQuery.data?.items ?? []).map((d) => ({
                value: d.id,
                label: d.is_active ? d.name : `${d.name} (неактивно)`,
              }))}
              value={directionId}
              onChange={setDirectionId}
            />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {mode === 'manual' && canRank ? (
              <p className="text-xs text-muted-foreground">
                Перетащите строку или используйте ▲/▼ — порядок сохраняется сразу
              </p>
            ) : null}
            {mode === 'manual' ? (
              <ExportMenu
                entityType="programs"
                fileName="programs"
                filters={() => ({
                  search: debouncedSearch.trim() || undefined,
                  product_id: productId,
                  university_id: universityId,
                  direction_id: directionId,
                })}
              />
            ) : (
              <ExportMenu
                entityType="report:programs-demand"
                fileName="programs-demand"
                filters={() => ({
                  ...demandPeriodRange(demandPeriod),
                  product_id: productId,
                  university_id: universityId,
                  direction_id: directionId,
                })}
              />
            )}
          </div>
        </div>
        {mode === 'manual' ? (
          manualBody
        ) : (
          <DemandView
            productId={productId}
            universityId={universityId}
            directionId={directionId}
            period={demandPeriod}
          />
        )}
      </div>
    </>
  );
}
