/**
 * Совместимость прежнего контракта RegistryTable (стадия Integrate):
 * канонические типы живут в shared/ui/data-table/types — здесь только
 * реэкспорт под старыми именами, чтобы не трогать API-слой
 * (shared/api/endpoints импортируют RegistryFetchParams по этому пути).
 */
export type {
  RegistryFilters,
  RegistryFetchParams,
  RegistryFetcher,
  RegistrySort,
  RegistryFilterContext,
} from '@/shared/ui/data-table/types';

export type {
  DataTableColumn as RegistryColumn,
  DataTableProps as RegistryTableProps,
} from '@/shared/ui/data-table/types';
