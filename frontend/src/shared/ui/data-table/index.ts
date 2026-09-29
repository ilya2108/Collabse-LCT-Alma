/**
 * Кит WP2 (redesign.md §3.2, §6.5, §6.9): DataTable + сопутствующие
 * компоненты мигрированных реестров и admin-таблиц.
 */
export { DataTable } from './data-table';
export { PresetsControl } from './presets-control';
export { ExportMenu } from './export-menu';
export {
  EmptyState,
  FilteredEmptyState,
  ErrorState,
  LoadingState,
  Illustration,
} from './empty-states';
export { StatusBadge, StageTag, ContractStatusTag } from './status-badge';
export { PageHeader } from './page-header';
export { SectionCard, DL, DLRow, MiniCard, StaggerGrid, StaggerItem } from './detail-blocks';
export {
  Combobox,
  type ComboboxOption,
  MultiCombobox,
  UniversityCombobox,
  ProductCombobox,
  ProductMultiCombobox,
  UserCombobox,
} from './combobox';
export { DateRangePicker, DatePickerField, type DateRangeValue } from './date-range-picker';
export { SegmentedControl } from './segmented';
export { FilterSelect } from './filter-select';
export type {
  DataTableColumn,
  DataTableProps,
  RegistryFetchParams,
  RegistryFetcher,
  RegistryFilters,
  RegistryFilterContext,
  RegistrySort,
  IllustrationName,
} from './types';
