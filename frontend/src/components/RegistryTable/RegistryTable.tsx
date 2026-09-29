/**
 * Совместимость до конца миграции (redesign.md §3.2): RegistryTable —
 * реэкспорт нового DataTable (TanStack) с прежним именем; контракт пропсов
 * совпадает 1-в-1. Новый код импортирует DataTable из
 * '@/shared/ui/data-table' напрямую.
 */
export { DataTable as RegistryTable } from '@/shared/ui/data-table';
