import type { ReactNode } from 'react';
import type { QueryValue } from '@/shared/api/client';
import type { ListEnvelope } from '@/shared/api/types';

/**
 * Контракт DataTable (redesign.md §3.2) — API-совместим с прежним
 * RegistryTableProps 1-в-1: миграция страниц — заменой импорта.
 * Типы структурно совпадают с components/RegistryTable/types.ts,
 * поэтому существующие fetcher'ы из shared/api/endpoints подходят как есть.
 */

/** Значения фильтров реестра — плоские query-параметры API (api-contract.md §1.5). */
export type RegistryFilters = Record<string, QueryValue | QueryValue[]>;

/** Параметры запроса списка, которые DataTable передаёт в fetcher. */
export interface RegistryFetchParams {
  limit: number;
  offset: number;
  /** Формат API: `-field` — по убыванию (api-contract.md §1.5). */
  sort?: string;
  search?: string;
  filters: RegistryFilters;
  signal?: AbortSignal;
}

export type RegistryFetcher<T> = (params: RegistryFetchParams) => Promise<ListEnvelope<T>>;

export interface RegistrySort {
  field: string;
  order: 'asc' | 'desc';
}

/** Сюжет иллюстрации пустого состояния (redesign.md §4.2). */
export type IllustrationName =
  | 'board-empty'
  | 'registry-empty'
  | 'students-empty'
  | 'search-empty'
  | 'import-drop'
  | 'import-done'
  | 'login-hero'
  | 'error-broken'
  | 'error-403'
  | 'error-404'
  | 'onboarding-welcome'
  | 'notifications-empty';

/** Колонка DataTable (§3.2): key обязателен (сортировка, видимость, пресеты). */
export interface DataTableColumn<T> {
  key: string;
  title: string;
  /** По умолчанию = key. */
  dataIndex?: keyof T & string;
  render?: (value: unknown, record: T) => ReactNode;
  /** Серверная сортировка по key. */
  sorter?: boolean;
  width?: number | string;
  align?: 'left' | 'center' | 'right';
  /** truncate + title. */
  ellipsis?: boolean;
  /** Показывать только с указанного брейкпоинта (скрыть уже него). */
  responsive?: Array<'md' | 'lg' | 'xl'>;
  /** Нельзя скрыть в меню «Колонки». */
  alwaysVisible?: boolean;
}

/** Рендер-проп панели фильтров над таблицей. */
export interface RegistryFilterContext {
  filters: RegistryFilters;
  setFilter: (key: string, value: QueryValue | QueryValue[]) => void;
}

export interface DataTableProps<T> {
  /** Ключ экрана для пресетов, например `registry.universities` (ux.md §4.2). */
  screen: string;
  columns: DataTableColumn<T>[];
  rowKey: keyof T & string;
  fetcher: RegistryFetcher<T>;
  searchPlaceholder?: string;
  renderFilters?: (ctx: RegistryFilterContext) => ReactNode;
  defaultFilters?: RegistryFilters;
  defaultSort?: RegistrySort | null;
  /** Действия справа в панели (primary-кнопка экрана). */
  toolbar?: ReactNode;
  /** entity_type для кнопки «Экспорт» через /export/jobs; не задан — нет кнопки. */
  exportEntityType?: string;
  emptyTitle?: string;
  emptyDescription?: ReactNode;
  emptyAction?: ReactNode;
  /** Сюжет пустого состояния (§4). */
  emptyIllustration?: IllustrationName;
  onRowClick?: (record: T) => void;
}
