import { useQuery } from '@tanstack/react-query';
import {
  CalendarClock,
  ChevronDown,
  Download,
  Flame,
  Info,
  LoaderCircle,
  MessagesSquare,
  School,
  Workflow as WorkflowIcon,
} from 'lucide-react';
import { motion } from 'motion/react';
import { useCallback, useMemo, useState, type ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import { RequestDrawer } from '@/components/requests/RequestDrawer';
import {
  exportInteractions,
  exportKamActivity,
  getInteractions,
  getKamActivity,
  getKamActivitySummary,
  type InteractionsColumnKey,
  type InteractionsExportFormat,
  type KamActivityExportFormat,
  type KamActivityReport,
} from '@/shared/api/endpoints/kamActivity';
import { useAuth } from '@/shared/auth/AuthContext';
import { staggerContainer, staggerItem } from '@/shared/lib/motion';
import { toastError, toastSuccess } from '@/shared/lib/toast';
import { useBreakpoint } from '@/shared/lib/useBreakpoint';
import { useSseInvalidate } from '@/shared/lib/useSseInvalidate';
import { Button } from '@/shared/ui/button';
import {
  EmptyState,
  ErrorState,
  FilteredEmptyState,
  LoadingState,
  PageHeader,
} from '@/shared/ui/data-table';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/shared/ui/dropdown-menu';
import { Popover, PopoverContent, PopoverTrigger } from '@/shared/ui/popover';
import { StatCard } from '@/shared/ui/stat-card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/shared/ui/tabs';
import { KamActivityCards } from './KamActivityCards';
import { KamActivityFilters, type FiltersPatch } from './KamActivityFilters';
import { KamActivityInteractions } from './KamActivityInteractions';
import { KamActivitySummary } from './KamActivitySummary';
import { KamActivityTree } from './KamActivityTree';
import {
  asOfText,
  filtersFromParams,
  formatDay,
  hasInteractionFilters,
  hasNarrowingFilters,
  presetRange,
  resolveColumns,
  toApiParams,
  toInteractionsParams,
  type KamActivityTab,
} from './kamActivityModel';

/**
 * Отчёт «Активность КАМов» (ux.md §6.4, api-contract.md §7.2.1): какой КАМ за период
 * с какими вузами взаимодействовал и на каких этапах остановились заявки.
 * Три вкладки — «Детализация» (руководителю КАМов), «Сводка для заказчика» и
 * «Взаимодействия за период» (таблица по колонкам ТЗ с выгрузкой XLSX/PDF) — с одной
 * семантикой периода; по умолчанию — «Месяц». Фильтры в URL (ссылку можно переслать),
 * карточка заявки — поверх отчёта (`request=`), выгрузки — серверные, через журнал выгрузок.
 */

const DEFINITIONS: Array<[string, string]> = [
  ['Период', 'С 00:00 МСК первого дня по 24:00 МСК последнего; «данные на» — конец периода или сейчас.'],
  [
    'Взаимодействие',
    'Собственное действие ответственного КАМа в периоде: ручной переход по этапам, комментарий, файл заявки, мероприятие с вузом. Системные записи (перевод схемы, LMS, импорт) и действия других пользователей не считаются.',
  ],
  ['Этап остановки', 'Этап заявки на конец периода, восстановленный по истории переходов.'],
  [
    'Дней на этапе',
    'От входа в этот этап до конца периода (или до сейчас). Перевод схемы процесса и события LMS срок не обнуляют — как на доске.',
  ],
  ['Зависла', 'Этап не финальный и заявка на нём дольше порога (этап → воронка → общий).'],
  ['Выиграно / отказ', 'Заявка вошла в финальный этап в периоде. Конверсия — выиграно / (выиграно + отказ).'],
  ['Охват', 'Закреплённые вузы, с которыми было хоть одно взаимодействие, / все закреплённые.'],
  [
    'Вузы по этапам',
    'Каждый вуз — один раз, по самому продвинутому этапу его B2B-заявок на конец периода; «Отказ» — только если других заявок нет.',
  ],
  [
    'Вузы с договором',
    'Договор действует на конец периода: статус «Действует» (или завершён, но срок ещё не истёк) и дата в сроке действия.',
  ],
  [
    'Чья заявка',
    'Заявка числится за текущим ответственным; в карточке заявки видно, кто и что делал.',
  ],
];

function HowItWorks(): ReactNode {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="sm" className="h-7 px-2 text-muted-foreground">
          <Info aria-hidden="true" />
          Как считается
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[min(92vw,420px)]">
        <dl className="flex flex-col gap-2 text-sm">
          {DEFINITIONS.map(([term, text]) => (
            <div key={term}>
              <dt className="font-medium">{term}</dt>
              <dd className="text-muted-foreground">{text}</dd>
            </div>
          ))}
        </dl>
      </PopoverContent>
    </Popover>
  );
}

function DetailKpis({ report, loading }: { report: KamActivityReport | null; loading: boolean }): ReactNode {
  const totals = report?.totals;
  const pending = loading || !totals;
  return (
    <motion.div
      variants={staggerContainer}
      initial="hidden"
      animate="visible"
      className="grid grid-cols-1 gap-3 min-[440px]:grid-cols-2 xl:grid-cols-4"
    >
      <motion.div variants={staggerItem}>
        <StatCard
          title="Вузов с контактом"
          icon={School}
          tone="primary"
          value={totals?.assigned_touched ?? 0}
          hint={`${totals?.assigned_touched ?? 0} из ${totals?.universities_assigned ?? 0} закреплённых; всего вузов с взаимодействиями — ${totals?.universities_touched ?? 0}`}
          loading={pending}
        />
      </motion.div>
      <motion.div variants={staggerItem}>
        <StatCard
          title="Заявок в работе"
          icon={WorkflowIcon}
          tone="primary"
          value={totals?.requests_count ?? 0}
          hint={`Создано в периоде: ${totals?.new_requests ?? 0}; выиграно ${totals?.won ?? 0}, отказов ${totals?.lost ?? 0}`}
          loading={pending}
        />
      </motion.div>
      <motion.div variants={staggerItem}>
        <StatCard
          title="Взаимодействий"
          icon={MessagesSquare}
          tone="success"
          value={totals?.interactions.total ?? 0}
          hint="Собственные действия КАМов: переходы, комментарии, файлы, мероприятия"
          loading={pending}
        />
      </motion.div>
      <motion.div variants={staggerItem}>
        <StatCard
          title="Зависших на конец периода"
          icon={Flame}
          tone="warning"
          value={totals?.stuck ?? 0}
          invertedDelta
          hint="Нефинальный этап дольше порога"
          loading={pending}
        />
      </motion.div>
    </motion.div>
  );
}

export function KamActivityScreen(): ReactNode {
  const { hasPermission, profile, workflowTypes } = useAuth();
  const { md } = useBreakpoint();
  const [searchParams, setSearchParams] = useSearchParams();
  const defaultTab: KamActivityTab = profile === 'observer' ? 'summary' : 'detail';
  const filters = useMemo(
    () => filtersFromParams(searchParams, { tab: defaultTab }),
    [searchParams, defaultTab],
  );
  const params = useMemo(() => toApiParams(filters), [filters]);
  const interactionParams = useMemo(() => toInteractionsParams(filters), [filters]);
  const [exporting, setExporting] = useState<KamActivityExportFormat | null>(null);
  const [exportingTable, setExportingTable] = useState<InteractionsExportFormat | null>(null);

  const canSeeAll = hasPermission('reports:all');
  const canFilterByKam = canSeeAll && hasPermission('admin:users');

  const patchParams = useCallback(
    (patch: Record<string, string | string[] | null>) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          for (const [key, value] of Object.entries(patch)) {
            next.delete(key);
            if (Array.isArray(value)) {
              for (const item of value) next.append(key, item);
            } else if (value) {
              next.set(key, value);
            }
          }
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const onFiltersChange = (patch: FiltersPatch): void => {
    const next: Record<string, string | string[] | null> = {};
    if (patch.preset !== undefined) {
      next.preset = patch.preset;
      if (patch.preset === 'custom') {
        // «Период…» стартует с текущего диапазона пресета — его удобно подправить
        const current = filters.preset === 'custom' ? filters : presetRange(filters.preset);
        next.from = patch.from !== undefined ? patch.from : current.from;
        next.to = patch.to !== undefined ? patch.to : current.to;
      } else {
        next.from = null;
        next.to = null;
      }
    }
    if (patch.wf !== undefined) next.wf = patch.wf;
    if (patch.kam !== undefined) next.kam = patch.kam;
    if (patch.university !== undefined) next.university = patch.university;
    if (patch.product !== undefined) next.product = patch.product;
    if (patch.program !== undefined) next.program = patch.program;
    if (patch.status !== undefined) next.status = patch.status;
    patchParams(next);
  };

  const resetFilters = (): void =>
    patchParams({
      preset: null,
      from: null,
      to: null,
      wf: null,
      kam: [],
      university: [],
      product: [],
      program: [],
      status: [],
    });

  const detailQuery = useQuery({
    queryKey: ['kam-activity', params],
    queryFn: ({ signal }) => getKamActivity(params, signal),
    staleTime: 30_000,
  });
  const summaryQuery = useQuery({
    queryKey: ['kam-activity-summary', params],
    queryFn: ({ signal }) => getKamActivitySummary(params, signal),
    staleTime: 30_000,
    enabled: filters.tab === 'summary',
  });
  const interactionsQuery = useQuery({
    queryKey: ['kam-activity-interactions', interactionParams],
    queryFn: ({ signal }) => getInteractions(interactionParams, signal),
    staleTime: 30_000,
    enabled: filters.tab === 'interactions',
  });
  const reportKeys = [['kam-activity'], ['kam-activity-summary'], ['kam-activity-interactions']];
  useSseInvalidate('workflow.changed', reportKeys);
  useSseInvalidate('request.transitioned', reportKeys);
  useSseInvalidate('request.comment_added', reportKeys);

  const report = detailQuery.data ?? null;
  const ownOnly = report?.scope.own_only ?? !canSeeAll;
  const title = ownOnly ? 'Моя активность' : 'Активность КАМов';

  const openRequest = useCallback((id: string) => patchParams({ request: id }), [patchParams]);
  const closeRequest = useCallback(() => patchParams({ request: null }), [patchParams]);
  const selectKam = useCallback(
    (kamId: string) => patchParams({ tab: 'detail', kam: [kamId] }),
    [patchParams],
  );

  const runExport = async (format: KamActivityExportFormat): Promise<void> => {
    setExporting(format);
    try {
      const name = await exportKamActivity(format, params);
      toastSuccess('Файл выгружен', name);
    } catch (error) {
      toastError(error, { title: 'Не удалось выгрузить отчёт' });
    } finally {
      setExporting(null);
    }
  };

  const interactions = interactionsQuery.data ?? null;
  const tableColumns: InteractionsColumnKey[] = interactions
    ? resolveColumns(filters.columns, interactions.columns, interactions.default_columns)
    : [];
  const setTableColumns = (columns: InteractionsColumnKey[] | null): void =>
    patchParams({ cols: columns ? columns.join(',') : null });

  const runTableExport = async (format: InteractionsExportFormat): Promise<void> => {
    setExportingTable(format);
    try {
      const name = await exportInteractions(
        format,
        interactionParams,
        tableColumns.length > 0 ? tableColumns : (interactions?.default_columns ?? []),
      );
      toastSuccess('Файл выгружен', name);
    } catch (error) {
      toastError(error, { title: 'Не удалось выгрузить отчёт' });
    } finally {
      setExportingTable(null);
    }
  };

  const exportMenu = (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" disabled={exporting !== null || exportingTable !== null}>
          {exporting || exportingTable ? (
            <LoaderCircle className="animate-spin" aria-hidden="true" />
          ) : (
            <Download aria-hidden="true" />
          )}
          Экспорт
          <ChevronDown className="text-muted-foreground" aria-hidden="true" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onSelect={() => void runExport('pdf')}>PDF для заказчика</DropdownMenuItem>
        <DropdownMenuItem onSelect={() => void runExport('xlsx')}>XLSX — все листы</DropdownMenuItem>
        <DropdownMenuItem onSelect={() => void runExport('csv')}>CSV — список заявок</DropdownMenuItem>
        <DropdownMenuItem onSelect={() => void runTableExport('xlsx')}>
          XLSX — взаимодействия по колонкам
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => void runTableExport('pdf')}>
          PDF — взаимодействия по колонкам
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );

  const period = report?.period;
  const periodCaption = period ? (
    <span className="tabular">
      Период: {formatDay(period.from)} — {formatDay(period.to)} · {asOfText(period)}
    </span>
  ) : (
    <span>Период загружается…</span>
  );

  const isEmpty = report !== null && report.rows.every((row) => row.universities.length === 0);
  const filtered = hasNarrowingFilters(filters);

  let interactionsBody: ReactNode;
  if (interactionsQuery.isError) {
    interactionsBody = (
      <ErrorState error={interactionsQuery.error} onRetry={() => void interactionsQuery.refetch()} />
    );
  } else if (interactionsQuery.isLoading || !interactions) {
    interactionsBody = <LoadingState rows={8} />;
  } else if (interactions.rows.length === 0 && hasInteractionFilters(filters)) {
    interactionsBody = <FilteredEmptyState onReset={resetFilters} />;
  } else if (interactions.rows.length === 0) {
    interactionsBody = (
      <EmptyState
        title="За выбранный период взаимодействий нет"
        description="Ни одной заявки в работе за период. Попробуйте полный прошлый квартал."
        actions={
          <Button onClick={() => onFiltersChange({ preset: 'prev_quarter' })}>
            Показать прошлый квартал
          </Button>
        }
      />
    );
  } else {
    interactionsBody = (
      <KamActivityInteractions
        report={interactions}
        columns={tableColumns}
        onColumnsChange={setTableColumns}
        onOpenRequest={openRequest}
        onExport={(format) => void runTableExport(format)}
        exporting={exportingTable}
      />
    );
  }

  let detailBody: ReactNode;
  if (detailQuery.isError) {
    detailBody = <ErrorState error={detailQuery.error} onRetry={() => void detailQuery.refetch()} />;
  } else if (detailQuery.isLoading || !report) {
    detailBody = <LoadingState rows={8} />;
  } else if (isEmpty && filtered) {
    detailBody = <FilteredEmptyState onReset={resetFilters} />;
  } else if (isEmpty) {
    detailBody = (
      <EmptyState
        title="За выбранный период взаимодействий нет"
        description="Ни заявок, ни закреплённых вузов в этом периоде. Попробуйте полный прошлый квартал."
        actions={
          <Button onClick={() => onFiltersChange({ preset: 'prev_quarter' })}>
            Показать прошлый квартал
          </Button>
        }
      />
    );
  } else if (md) {
    detailBody = <KamActivityTree report={report} params={params} onOpenRequest={openRequest} />;
  } else {
    detailBody = <KamActivityCards report={report} params={params} onOpenRequest={openRequest} />;
  }

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <PageHeader
        title={title}
        subtitle={
          ownOnly
            ? 'С какими вузами вы работали за период и на каких этапах остановились заявки'
            : 'Кто из КАМов, когда и с какими вузами работал — и на каких этапах остановились заявки'
        }
        extra={exportMenu}
      />

      <KamActivityFilters
        filters={filters}
        onChange={onFiltersChange}
        onReset={resetFilters}
        workflowTypes={workflowTypes}
        canFilterByKam={canFilterByKam}
        interactionFilters={filters.tab === 'interactions'}
        stageOptions={interactions?.stages ?? []}
      />

      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-muted-foreground">
        <span className="min-w-0">
          <CalendarClock className="mr-1.5 inline size-4 align-[-3px]" aria-hidden="true" />
          {periodCaption}
          {ownOnly ? <span className="text-xs"> · только ваши заявки</span> : null}
        </span>
        <HowItWorks />
      </div>

      <Tabs
        value={filters.tab}
        onValueChange={(value) => patchParams({ tab: value === defaultTab ? null : value })}
      >
        <div className="max-w-full overflow-x-auto">
          <TabsList>
            <TabsTrigger value="detail">Детализация</TabsTrigger>
            <TabsTrigger value="summary">Сводка для заказчика</TabsTrigger>
            <TabsTrigger value="interactions">Взаимодействия за период</TabsTrigger>
          </TabsList>
        </div>
        <TabsContent value="detail" className="flex min-w-0 flex-col gap-4">
          {detailQuery.isError ? null : <DetailKpis report={report} loading={detailQuery.isLoading} />}
          {detailBody}
        </TabsContent>
        <TabsContent value="summary" className="min-w-0">
          {summaryQuery.isError ? (
            <ErrorState
              error={summaryQuery.error}
              onRetry={() => void summaryQuery.refetch()}
              title="Не удалось загрузить сводку"
            />
          ) : (
            <KamActivitySummary
              summary={summaryQuery.data ?? null}
              loading={summaryQuery.isLoading}
              onOpenRequest={openRequest}
              onSelectKam={selectKam}
              onExportPdf={() => void runExport('pdf')}
              exporting={exporting === 'pdf'}
            />
          )}
        </TabsContent>
        <TabsContent value="interactions" className="min-w-0">
          {interactionsBody}
        </TabsContent>
      </Tabs>

      <RequestDrawer requestId={filters.request} onClose={closeRequest} />
    </div>
  );
}

export default KamActivityScreen;
