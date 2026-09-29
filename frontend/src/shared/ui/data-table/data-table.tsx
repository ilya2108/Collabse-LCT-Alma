import { keepPreviousData, useQuery } from '@tanstack/react-query';
import {
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from '@tanstack/react-table';
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  ChevronLeft,
  ChevronRight,
  Search,
  Settings2,
  X,
} from 'lucide-react';
import { useCallback, useMemo, useRef, useState, type ReactNode } from 'react';
import { listPresets } from '@/shared/api/endpoints/presets';
import type { QueryValue } from '@/shared/api/client';
import type { UiPreset, UiPresetState } from '@/shared/api/types';
import { cn } from '@/shared/lib/cn';
import { useBreakpoint, type Breakpoints } from '@/shared/lib/useBreakpoint';
import { useDebouncedValue } from '@/shared/lib/useDebouncedValue';
import { Button } from '@/shared/ui/button';
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from '@/shared/ui/dropdown-menu';
import { Input } from '@/shared/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/ui/table';
import { EmptyState, ErrorState, FilteredEmptyState, LoadingState } from './empty-states';
import { ExportMenu } from './export-menu';
import { PresetsControl } from './presets-control';
import type { DataTableColumn, DataTableProps, RegistryFilters, RegistrySort } from './types';

/**
 * DataTable на TanStack Table v8 (redesign.md §3.2) — замена RegistryTable
 * с тем же контрактом пропсов: серверные пагинация/сортировка/поиск,
 * пресеты /ui/presets, видимость колонок, состояния ux.md §4.1.
 * Строки НЕ стаггерятся (§2.3: ежедневный инструмент — скорость важнее).
 */

const PAGE_SIZE_OPTIONS = [20, 50, 100];

interface TableState {
  page: number;
  pageSize: number;
  sort: RegistrySort | null;
  search: string;
  filters: RegistryFilters;
}

function sortToApi(sort: RegistrySort | null): string | undefined {
  if (!sort) return undefined;
  return sort.order === 'desc' ? `-${sort.field}` : sort.field;
}

function hasValue(value: unknown): boolean {
  if (value === null || value === undefined || value === '') return false;
  if (Array.isArray(value)) return value.length > 0;
  return true;
}

function isColumnVisibleAtBreakpoint<T>(column: DataTableColumn<T>, bp: Breakpoints): boolean {
  if (!column.responsive || column.responsive.length === 0) return true;
  return column.responsive.every((key) => bp[key]);
}

export function DataTable<T extends object>({
  screen,
  columns,
  rowKey,
  fetcher,
  searchPlaceholder = 'Поиск',
  renderFilters,
  defaultFilters = {},
  defaultSort = null,
  toolbar,
  exportEntityType,
  emptyTitle = 'Записей пока нет',
  emptyDescription,
  emptyAction,
  emptyIllustration = 'registry-empty',
  onRowClick,
}: DataTableProps<T>): ReactNode {
  // Дефолты применяются один раз на маунте: дальше состояние живёт своё.
  const [state, setState] = useState<TableState>(() => ({
    page: 1,
    pageSize: PAGE_SIZE_OPTIONS[0] ?? 20,
    sort: defaultSort,
    search: '',
    filters: defaultFilters,
  }));
  const [visibleKeys, setVisibleKeys] = useState<string[]>(() => columns.map((c) => c.key));
  const [activePresetId, setActivePresetId] = useState<string | null>(null);
  // Поиск уходит на сервер после 400 мс тишины, а не на каждый символ.
  const debouncedSearch = useDebouncedValue(state.search, 400);
  const bp = useBreakpoint();

  // --- Пресеты -------------------------------------------------------------
  const presetsQuery = useQuery({
    queryKey: ['ui-presets', screen],
    queryFn: ({ signal }) => listPresets(screen, signal),
    staleTime: 60_000,
    retry: 0,
  });
  const presets = presetsQuery.data?.items ?? [];

  const applyPreset = useCallback((preset: UiPreset) => {
    const presetState = preset.state;
    setState((prev) => ({
      page: 1,
      pageSize: presetState.page_size ?? prev.pageSize,
      sort: presetState.sort ?? null,
      search: prev.search,
      filters: (presetState.filters as RegistryFilters | undefined) ?? {},
    }));
    if (presetState.columns && presetState.columns.length > 0) {
      setVisibleKeys(presetState.columns);
    }
    setActivePresetId(preset.id);
  }, []);

  // Пресет «по умолчанию» применяется один раз после загрузки списка.
  const defaultAppliedRef = useRef(false);
  const defaultPreset = presets.find((p) => p.is_default);
  if (!defaultAppliedRef.current && defaultPreset) {
    defaultAppliedRef.current = true;
    applyPreset(defaultPreset);
  }

  const currentPresetState = useCallback(
    (): UiPresetState => ({
      filters: state.filters as Record<string, unknown>,
      sort: state.sort,
      columns: visibleKeys,
      page_size: state.pageSize,
    }),
    [state.filters, state.sort, state.pageSize, visibleKeys],
  );

  // --- Данные --------------------------------------------------------------
  const fetchParams = useMemo(
    () => ({
      limit: state.pageSize,
      offset: (state.page - 1) * state.pageSize,
      sort: sortToApi(state.sort),
      search: debouncedSearch.trim() || undefined,
      filters: state.filters,
    }),
    [state.pageSize, state.page, state.sort, state.filters, debouncedSearch],
  );

  const dataQuery = useQuery({
    queryKey: ['registry', screen, fetchParams],
    // signal react-query прокидывается в fetch: смена параметров отменяет
    // предыдущий запрос — гонка «старый ответ перетёр новый» невозможна.
    queryFn: ({ signal }) => fetcher({ ...fetchParams, signal }),
    // при смене страницы/фильтров показываем прежние строки до прихода новых
    placeholderData: keepPreviousData,
  });

  const filtersActive =
    Boolean(debouncedSearch.trim()) || Object.values(state.filters).some(hasValue);

  const setFilter = useCallback((key: string, value: QueryValue | QueryValue[]) => {
    setState((prev) => ({ ...prev, page: 1, filters: { ...prev.filters, [key]: value } }));
  }, []);

  const resetFilters = useCallback(() => {
    setState((prev) => ({ ...prev, page: 1, search: '', filters: {} }));
  }, []);

  const toggleSort = useCallback((field: string) => {
    setState((prev) => {
      if (prev.sort?.field !== field) return { ...prev, page: 1, sort: { field, order: 'asc' } };
      if (prev.sort.order === 'asc') return { ...prev, page: 1, sort: { field, order: 'desc' } };
      return { ...prev, page: 1, sort: null };
    });
  }, []);

  // --- Колонки -------------------------------------------------------------
  const displayedColumns = useMemo(
    () =>
      columns.filter(
        (column) =>
          (column.alwaysVisible || visibleKeys.includes(column.key)) &&
          isColumnVisibleAtBreakpoint(column, bp),
      ),
    [columns, visibleKeys, bp],
  );

  // TanStack-модель: manualPagination/manualSorting — состояние серверное.
  const tanstackColumns = useMemo<ColumnDef<T>[]>(
    () =>
      displayedColumns.map((column) => ({
        id: column.key,
        accessorFn: (row: T) => (row as Record<string, unknown>)[column.dataIndex ?? column.key],
        enableSorting: Boolean(column.sorter),
      })),
    [displayedColumns],
  );

  const sorting: SortingState = state.sort
    ? [{ id: state.sort.field, desc: state.sort.order === 'desc' }]
    : [];

  const items = dataQuery.data?.items ?? [];
  const total = dataQuery.data?.total ?? 0;

  const table = useReactTable<T>({
    data: items,
    columns: tanstackColumns,
    state: { sorting },
    manualPagination: true,
    manualSorting: true,
    manualFiltering: true,
    getCoreRowModel: getCoreRowModel(),
    getRowId: (row) => String((row as Record<string, unknown>)[rowKey]),
  });

  // --- Рендер --------------------------------------------------------------
  const isFirstLoading = dataQuery.isLoading;
  const isRefreshing = dataQuery.isFetching && !dataQuery.isLoading;
  const showAbsoluteEmpty = !isFirstLoading && !dataQuery.isError && total === 0 && !filtersActive;

  const from = total === 0 ? 0 : (state.page - 1) * state.pageSize + 1;
  const to = Math.min(state.page * state.pageSize, total);
  const lastPage = Math.max(1, Math.ceil(total / state.pageSize));

  const renderCell = (column: DataTableColumn<T>, record: T): ReactNode => {
    const raw = (record as Record<string, unknown>)[column.dataIndex ?? column.key];
    const content = column.render ? column.render(raw, record) : ((raw ?? '—') as ReactNode);
    if (column.ellipsis) {
      return (
        <span className="block min-w-0 max-w-[280px] truncate" title={typeof raw === 'string' ? raw : undefined}>
          {content}
        </span>
      );
    }
    return content;
  };

  let body: ReactNode;
  if (dataQuery.isError) {
    body = <ErrorState error={dataQuery.error} onRetry={() => void dataQuery.refetch()} />;
  } else if (isFirstLoading) {
    // скелетон вместо спиннера на первичной загрузке (ux.md §4.1)
    body = (
      <div className="py-2">
        <LoadingState rows={8} card={false} />
      </div>
    );
  } else if (showAbsoluteEmpty) {
    body = (
      <EmptyState
        illustration={emptyIllustration}
        title={emptyTitle}
        description={emptyDescription}
        actions={emptyAction}
      />
    );
  } else {
    body = (
      <>
        <div className="relative overflow-x-auto">
          {/* Тонкий индикатор обновления (§3.2): полоска над шапкой, без миганий */}
          <div
            aria-hidden="true"
            className={cn(
              'absolute inset-x-0 top-0 z-10 h-0.5 bg-primary transition-opacity duration-150',
              isRefreshing ? 'opacity-100' : 'opacity-0',
            )}
          />
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                {displayedColumns.map((column) => {
                  const sorted = state.sort?.field === column.key ? state.sort.order : null;
                  const alignClass =
                    column.align === 'right' ? 'text-right' : column.align === 'center' ? 'text-center' : '';
                  return (
                    <TableHead
                      key={column.key}
                      style={{ width: column.width }}
                      aria-sort={
                        column.sorter
                          ? sorted === 'asc'
                            ? 'ascending'
                            : sorted === 'desc'
                              ? 'descending'
                              : 'none'
                          : undefined
                      }
                      className={alignClass}
                    >
                      {column.sorter ? (
                        <button
                          type="button"
                          onClick={() => toggleSort(column.key)}
                          className={cn(
                            '-mx-1 inline-flex items-center gap-1 rounded-sm px-1 py-0.5 uppercase tracking-wide transition-colors duration-150 hover:text-foreground',
                            sorted && 'text-primary',
                          )}
                        >
                          {column.title}
                          {sorted === 'asc' ? (
                            <ArrowUp className="size-3.5" aria-hidden="true" />
                          ) : sorted === 'desc' ? (
                            <ArrowDown className="size-3.5" aria-hidden="true" />
                          ) : (
                            <ArrowUpDown className="size-3.5 opacity-50" aria-hidden="true" />
                          )}
                        </button>
                      ) : (
                        column.title
                      )}
                    </TableHead>
                  );
                })}
              </TableRow>
            </TableHeader>
            <TableBody>
              {table.getRowModel().rows.length === 0 ? (
                <TableRow className="hover:bg-transparent">
                  <TableCell colSpan={displayedColumns.length}>
                    <FilteredEmptyState onReset={resetFilters} />
                  </TableCell>
                </TableRow>
              ) : (
                table.getRowModel().rows.map((row) => (
                  <TableRow
                    key={row.id}
                    tabIndex={onRowClick ? 0 : undefined}
                    className={cn(onRowClick && 'cursor-pointer focus-visible:bg-primary-tint')}
                    onClick={onRowClick ? () => onRowClick(row.original) : undefined}
                    onKeyDown={
                      onRowClick
                        ? (e) => {
                            if (e.key === 'Enter' && e.target === e.currentTarget) {
                              onRowClick(row.original);
                            }
                          }
                        : undefined
                    }
                  >
                    {displayedColumns.map((column) => (
                      <TableCell
                        key={column.key}
                        className={cn(
                          column.align === 'right' && 'text-right tabular',
                          column.align === 'center' && 'text-center',
                        )}
                      >
                        {renderCell(column, row.original)}
                      </TableCell>
                    ))}
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
        {/* Футер-пагинация: «1–20 из 134», размер страницы, prev/next */}
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
          <p className="tabular text-sm text-muted-foreground" aria-live="polite">
            {total > 0 ? `${from}–${to} из ${total}` : 'Нет записей'}
          </p>
          <div className="flex items-center gap-2">
            <Select
              value={String(state.pageSize)}
              onValueChange={(value) =>
                setState((prev) => ({ ...prev, page: 1, pageSize: Number(value) }))
              }
            >
              <SelectTrigger size="sm" aria-label="Строк на странице">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PAGE_SIZE_OPTIONS.map((size) => (
                  <SelectItem key={size} value={String(size)}>
                    {size} / стр.
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              variant="outline"
              size="icon"
              className="size-8"
              aria-label="Предыдущая страница"
              disabled={state.page <= 1}
              onClick={() => setState((prev) => ({ ...prev, page: prev.page - 1 }))}
            >
              <ChevronLeft aria-hidden="true" />
            </Button>
            <span className="tabular text-sm text-muted-foreground">
              {state.page} / {lastPage}
            </span>
            <Button
              variant="outline"
              size="icon"
              className="size-8"
              aria-label="Следующая страница"
              disabled={state.page >= lastPage}
              onClick={() => setState((prev) => ({ ...prev, page: prev.page + 1 }))}
            >
              <ChevronRight aria-hidden="true" />
            </Button>
          </div>
        </div>
      </>
    );
  }

  return (
    <div className="rounded-lg border bg-card p-5 shadow-card">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <PresetsControl
            screen={screen}
            presets={presets}
            presetsAvailable={!presetsQuery.isError}
            activePresetId={activePresetId}
            currentState={currentPresetState}
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
              className="w-[240px] pl-8"
              placeholder={searchPlaceholder}
              aria-label={searchPlaceholder}
              value={state.search}
              onChange={(e) => setState((prev) => ({ ...prev, page: 1, search: e.target.value }))}
            />
          </div>
          {renderFilters?.({ filters: state.filters, setFilter })}
          {filtersActive ? (
            <Button variant="ghost" onClick={resetFilters}>
              <X aria-hidden="true" />
              Сбросить
            </Button>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline">
                <Settings2 aria-hidden="true" />
                Колонки
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuLabel>Видимые колонки</DropdownMenuLabel>
              {columns.map((column) => (
                <DropdownMenuCheckboxItem
                  key={column.key}
                  disabled={column.alwaysVisible}
                  checked={column.alwaysVisible || visibleKeys.includes(column.key)}
                  onCheckedChange={(checked) =>
                    setVisibleKeys((prev) =>
                      checked ? [...prev, column.key] : prev.filter((k) => k !== column.key),
                    )
                  }
                  onSelect={(e) => e.preventDefault()}
                >
                  {column.title}
                </DropdownMenuCheckboxItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
          {exportEntityType ? (
            <ExportMenu
              entityType={exportEntityType}
              fileName={exportEntityType}
              filters={() => ({
                search: debouncedSearch.trim() || undefined,
                ...state.filters,
              })}
            />
          ) : null}
          {toolbar}
        </div>
      </div>
      {body}
    </div>
  );
}
