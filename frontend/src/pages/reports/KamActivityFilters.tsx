import { useQuery } from '@tanstack/react-query';
import { RotateCcw } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { listKamOptions, type InteractionsStageOption } from '@/shared/api/endpoints/kamActivity';
import { listActiveProducts } from '@/shared/api/endpoints/products';
import { listPrograms } from '@/shared/api/endpoints/programs';
import { searchUniversities } from '@/shared/api/endpoints/universities';
import type { WorkflowType } from '@/shared/api/types';
import { useDebouncedValue } from '@/shared/lib/useDebouncedValue';
import { Button } from '@/shared/ui/button';
import { DateRangeFilter, MultiFilter } from '../dashboard/FilterControls';
import { Segmented } from '../dashboard/Segmented';
import { PRESETS, type KamActivityFilters as Filters, type PeriodPreset } from './kamActivityModel';

/**
 * Панель фильтров отчёта (ux.md §6.4): пресеты периода, процесс B2B/B2C/Все
 * (только доступные роли процессы), КАМ (для права «все КАМы»), вуз с серверным
 * поиском и «Сбросить». На вкладке «Взаимодействия за период» — ещё ИТ-продукт,
 * программа и статус (этап на конец периода). На мобильных пресеты прокручиваются
 * внутри своей полосы — без горизонтальной прокрутки страницы.
 */

export type FiltersPatch = Partial<{
  preset: PeriodPreset;
  from: string | null;
  to: string | null;
  wf: WorkflowType | null;
  kam: string[];
  university: string[];
  product: string[];
  program: string[];
  status: string[];
}>;

interface KamActivityFiltersProps {
  filters: Filters;
  onChange: (patch: FiltersPatch) => void;
  onReset: () => void;
  /** Процессы, доступные роли (переключатель показывает только их). */
  workflowTypes: WorkflowType[];
  /** Фильтр по КАМам: право «отчёты по всем КАМам» + список пользователей. */
  canFilterByKam: boolean;
  /** Справа в строке — экспорт. */
  extra?: ReactNode;
  /** Фильтры вкладки «Взаимодействия за период»: продукт, программа, статус. */
  interactionFilters?: boolean;
  /** Варианты статуса — этапы процессов отчёта (приходят в ответе отчёта). */
  stageOptions?: InteractionsStageOption[];
}

export function KamActivityFilters({
  filters,
  onChange,
  onReset,
  workflowTypes,
  canFilterByKam,
  extra,
  interactionFilters = false,
  stageOptions = [],
}: KamActivityFiltersProps): ReactNode {
  const [universitySearch, setUniversitySearch] = useState('');
  const debouncedUniversity = useDebouncedValue(universitySearch, 300);
  const [programSearch, setProgramSearch] = useState('');
  const debouncedProgram = useDebouncedValue(programSearch, 300);
  const productsQuery = useQuery({
    queryKey: ['products-select', 'active'],
    queryFn: ({ signal }) => listActiveProducts(signal),
    enabled: interactionFilters,
    staleTime: 60_000,
  });
  const programsQuery = useQuery({
    queryKey: ['programs-select', debouncedProgram],
    queryFn: ({ signal }) =>
      listPrograms({ limit: 50, offset: 0, search: debouncedProgram || undefined, filters: {}, signal }),
    enabled: interactionFilters,
    staleTime: 30_000,
  });
  const multiWorkflow = new Set(stageOptions.map((stage) => stage.workflow_type)).size > 1;
  const universitiesQuery = useQuery({
    queryKey: ['universities-select', debouncedUniversity],
    queryFn: ({ signal }) => searchUniversities(debouncedUniversity, signal),
    staleTime: 30_000,
  });
  const kamsQuery = useQuery({
    queryKey: ['users-select', 'permission', 'requests:kam'],
    queryFn: ({ signal }) => listKamOptions(signal),
    enabled: canFilterByKam,
    staleTime: 60_000,
  });

  const workflowOptions = [
    ...workflowTypes.map((wf) => ({ value: wf, label: wf.toUpperCase() })),
    ...(workflowTypes.length > 1 ? [{ value: 'all', label: 'Все' }] : []),
  ];

  return (
    <div className="flex flex-col gap-2" data-tour="kam-activity-filters">
      <div className="-mx-1 max-w-full overflow-x-auto px-1 pb-0.5 [&_button]:whitespace-nowrap">
        <Segmented
          aria-label="Период отчёта"
          value={filters.preset}
          onChange={(value) => onChange({ preset: value as PeriodPreset })}
          options={PRESETS}
        />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {filters.preset === 'custom' ? (
          <DateRangeFilter
            from={filters.from}
            to={filters.to}
            onChange={(from, to) => onChange({ preset: 'custom', from, to })}
          />
        ) : null}
        {workflowOptions.length > 1 ? (
          <Segmented
            aria-label="Тип процесса"
            value={filters.wf ?? 'all'}
            onChange={(value) => onChange({ wf: value === 'all' ? null : (value as WorkflowType) })}
            options={workflowOptions}
          />
        ) : null}
        {canFilterByKam ? (
          <MultiFilter
            placeholder="КАМ"
            options={(kamsQuery.data?.items ?? []).map((user) => ({
              value: user.id,
              label: user.full_name || user.username,
            }))}
            value={filters.kam}
            onChange={(value) => onChange({ kam: value })}
            loading={kamsQuery.isLoading}
            emptyText="КАМы не найдены"
          />
        ) : null}
        <MultiFilter
          placeholder="Вуз"
          options={(universitiesQuery.data?.items ?? []).map((university) => ({
            value: university.id,
            label: university.short_name || university.name,
          }))}
          value={filters.university}
          onChange={(value) => onChange({ university: value })}
          onSearch={setUniversitySearch}
          loading={universitiesQuery.isLoading}
          emptyText="Вузы не найдены"
        />
        {interactionFilters ? (
          <>
            <MultiFilter
              placeholder="ИТ-продукт"
              options={(productsQuery.data?.items ?? []).map((product) => ({
                value: product.id,
                label: product.name,
              }))}
              value={filters.product}
              onChange={(value) => onChange({ product: value })}
              loading={productsQuery.isLoading}
              emptyText="Продукты не найдены"
            />
            <MultiFilter
              placeholder="Программа"
              options={(programsQuery.data?.items ?? []).map((program) => ({
                value: program.id,
                label: program.name,
              }))}
              value={filters.program}
              onChange={(value) => onChange({ program: value })}
              onSearch={setProgramSearch}
              loading={programsQuery.isLoading}
              emptyText="Программы не найдены"
            />
            <MultiFilter
              placeholder="Статус"
              options={stageOptions.map((stage) => ({
                value: stage.id,
                label: multiWorkflow ? `${stage.name} · ${stage.workflow_type.toUpperCase()}` : stage.name,
              }))}
              value={filters.status}
              onChange={(value) => onChange({ status: value })}
              emptyText="Этапы загружаются вместе с отчётом"
            />
          </>
        ) : null}
        <Button variant="ghost" onClick={onReset}>
          <RotateCcw aria-hidden="true" />
          Сбросить
        </Button>
        {extra ? <div className="ml-auto flex items-center gap-2">{extra}</div> : null}
      </div>
    </div>
  );
}
