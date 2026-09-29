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
import { ArrowDown, ArrowUp, GripVertical, ListOrdered, TrendingUp } from 'lucide-react';
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
import { Link } from 'react-router-dom';
import { api } from '@/shared/api/client';
import { listPrograms, reorderProgramPriorities } from '@/shared/api/endpoints/programs';
import { listActiveProducts } from '@/shared/api/endpoints/products';
import type { Program } from '@/shared/api/types';
import { useAuth } from '@/shared/auth/AuthContext';
import { useDebouncedValue } from '@/shared/lib/useDebouncedValue';
import { formatDate, formatText } from '@/shared/lib/format';
import { toastError } from '@/shared/lib/toast';
import { Badge } from '@/shared/ui/badge';
import { Button } from '@/shared/ui/button';
import {
  EmptyState,
  ErrorState,
  ExportMenu,
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
 * По заявкам» — SegmentedControl (§3.10).
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

// --- Режим «По заявкам» (отчёт programs-demand, §7.2) -------------------------

interface DemandSeriesItem {
  key: string;
  label: string;
  color?: string;
  value: number;
}

interface DemandReport {
  report: string;
  series: DemandSeriesItem[];
}

function DemandView({ productId }: { productId?: string }): ReactNode {
  const query = useQuery({
    queryKey: ['programs-demand', productId ?? 'all'],
    queryFn: ({ signal }) =>
      api.get<DemandReport>('/reports/programs-demand', {
        query: { product_id: productId },
        signal,
      }),
  });
  if (query.isLoading) return <LoadingState rows={6} card={false} />;
  if (query.isError) {
    return (
      <ErrorState
        error={query.error}
        title="Не удалось загрузить отчёт по заявкам"
        onRetry={() => void query.refetch()}
      />
    );
  }
  const series = [...(query.data?.series ?? [])].sort((a, b) => b.value - a.value);
  if (series.length === 0) {
    return <EmptyState illustration="search-empty" title="Нет данных по заявкам" compact />;
  }
  return (
    <Table>
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          <TableHead>Программа</TableHead>
          <TableHead className="w-[120px] text-right">Заявок</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {series.map((item) => (
          <TableRow key={item.key}>
            <TableCell>{item.label}</TableCell>
            <TableCell className="tabular text-right font-medium">{item.value}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

// --- Основной экран -----------------------------------------------------------

export function ProgramsPage(): ReactNode {
  const { hasPermission } = useAuth();
  const queryClient = useQueryClient();
  const canRank = hasPermission('programs:priority');

  const [mode, setMode] = useState<'manual' | 'demand'>('manual');
  const [search, setSearch] = useState('');
  const [productId, setProductId] = useState<string | undefined>(undefined);
  const [universityId, setUniversityId] = useState<string | undefined>(undefined);
  const debouncedSearch = useDebouncedValue(search, 400);

  const programsQuery = useQuery({
    queryKey: ['programs-ranked', debouncedSearch, productId ?? '', universityId ?? ''],
    queryFn: ({ signal }) =>
      listPrograms({
        limit: 200,
        offset: 0,
        sort: 'priority_rank',
        search: debouncedSearch.trim() || undefined,
        filters: { product_id: productId, university_id: universityId },
        signal,
      }),
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
                { value: 'demand', label: 'По заявкам', icon: TrendingUp },
              ]}
            />
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
            {mode === 'manual' ? (
              <UniversityCombobox
                className="w-[220px]"
                value={universityId}
                onChange={(value) => setUniversityId(value)}
              />
            ) : null}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {mode === 'manual' && canRank ? (
              <p className="text-xs text-muted-foreground">
                Перетащите строку или используйте ▲/▼ — порядок сохраняется сразу
              </p>
            ) : null}
            <ExportMenu
              entityType="programs"
              fileName="programs"
              filters={() => ({
                search: debouncedSearch.trim() || undefined,
                product_id: productId,
                university_id: universityId,
              })}
            />
          </div>
        </div>
        {mode === 'manual' ? manualBody : <DemandView productId={productId} />}
      </div>
    </>
  );
}
