import { useQuery } from '@tanstack/react-query';
import { ArrowRight, ChevronDown, Download, LoaderCircle } from 'lucide-react';
import { motion } from 'motion/react';
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { ErrorBrokenIllustration } from '@/app/localIllustrations';
import { listAllItDirections } from '@/shared/api/endpoints/itDirections';
import { listActiveProducts } from '@/shared/api/endpoints/products';
import { listPrograms } from '@/shared/api/endpoints/programs';
import { runExportAndDownload } from '@/shared/api/endpoints/exportJobs';
import {
  getDashboardReport,
  type DashboardData,
  type DashboardReportParams,
} from '@/shared/api/endpoints/reports';
import { searchUniversities } from '@/shared/api/endpoints/universities';
import { listUsers } from '@/shared/api/endpoints/users';
import { errorMessage, isApiError } from '@/shared/api/errors';
import { useAuth } from '@/shared/auth/AuthContext';
import { WORKFLOW_TYPE_LABELS } from '@/shared/auth/permissions';
import { dayjs } from '@/shared/lib/dayjs';
import { staggerContainer, staggerItem } from '@/shared/lib/motion';
import { toastError, toastSuccess } from '@/shared/lib/toast';
import { useDebouncedValue } from '@/shared/lib/useDebouncedValue';
import { useSseInvalidate } from '@/shared/lib/useSseInvalidate';
import { Button } from '@/shared/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/shared/ui/dropdown-menu';
import { ChartCard } from './ChartCard';
import { downloadDashboardPdf, downloadDashboardPng, type ChartExport } from './exportImage';
import { DateRangeFilter, MultiFilter } from './FilterControls';
import { ChecklistCard, useOnboarding } from '@/features/onboarding';
import { KpiCards, formatKpiPeriod } from './KpiCards';
import {
  PROGRAM_MODES,
  allZero,
  dynamicsOption,
  kamWorkloadOption,
  parseStageClickKey,
  programMetric,
  programsOption,
  rowsHeight,
  stageBarsHeight,
  stageBarsOption,
  talentBarsOption,
  type ProgramsMode,
} from './model';
import { Segmented } from './Segmented';

/**
 * Дашборд (redesign.md §6.3, ux.md §6): панель фильтров одной строкой над
 * сеткой (состояние в URL — ссылку можно переслать), KPI-ряд StatCard,
 * виджеты ECharts на теме 'crm' в сетке 12 колонок со stagger-входом,
 * drill-down кликами и экспорт: PNG и PDF всей панели (на клиенте, по графику на
 * страницу PDF), PNG каждого графика с шапкой, XLSX/CSV данных (через /export/jobs).
 *
 * Один набор фильтров на все секции: КАМы, вузы, ИТ-направления, продукты, программы —
 * списки (backend применяет IN везде). У каждой карточки подзаголовок — что и за какой
 * период показано (срез «сейчас» или период), чтобы цифры на экране не спорили.
 */

interface DashFilters {
  from: string | null;
  to: string | null;
  wf: 'b2b' | 'b2c' | null;
  kam: string[];
  university: string[];
  direction: string[];
  product: string[];
  program: string[];
}

type ListFilterKey = 'kam' | 'university' | 'direction' | 'product' | 'program';

function filtersFromParams(params: URLSearchParams): DashFilters {
  const wfRaw = params.get('wf');
  return {
    from: params.get('from'),
    to: params.get('to'),
    wf: wfRaw === 'b2b' || wfRaw === 'b2c' ? wfRaw : null,
    kam: params.getAll('kam'),
    university: params.getAll('university'),
    direction: params.getAll('direction'),
    product: params.getAll('product'),
    program: params.getAll('program'),
  };
}

function day(iso: string): string {
  return dayjs(iso).format('DD.MM.YYYY');
}

function periodText(f: Pick<DashFilters, 'from' | 'to'>): string | null {
  if (!f.from && !f.to) return null;
  return `${f.from ? day(f.from) : '…'} — ${f.to ? day(f.to) : '…'}`;
}

/**
 * Ссылка на отчёт «Активность КАМов» с фильтрами дашборда (URL-контракт ux.md §6.4):
 * заданный период — пресет «Произвольный», процесс, КАМы, вузы и ИТ-направления
 * переносятся как есть.
 */
function kamActivityHref(f: DashFilters): string {
  const query = new URLSearchParams();
  if (f.from || f.to) {
    query.set('preset', 'custom');
    if (f.from) query.set('from', f.from);
    if (f.to) query.set('to', f.to);
  }
  if (f.wf) query.set('wf', f.wf);
  for (const id of f.kam) query.append('kam', id);
  for (const id of f.university) query.append('university', id);
  for (const id of f.direction) query.append('direction', id);
  const search = query.toString();
  return search ? `/reports/kam-activity?${search}` : '/reports/kam-activity';
}

function reportParams(f: DashFilters): DashboardReportParams {
  return {
    workflow_type: f.wf ?? undefined,
    from: f.from ?? undefined,
    to: f.to ?? undefined,
    kam_id: f.kam.length > 0 ? f.kam : undefined,
    university_id: f.university.length > 0 ? f.university : undefined,
    product_id: f.product.length > 0 ? f.product : undefined,
    program_id: f.program.length > 0 ? f.program : undefined,
    direction_id: f.direction.length > 0 ? f.direction : undefined,
  };
}

/** Подзаголовки карточек: что и за какой период (срез «сейчас» — явно). */
function widgetSubtitles(f: DashFilters, report: DashboardData | null): Record<string, string> {
  const period = periodText(f);
  const dyn = report?.dynamics.period;
  return {
    stages: period
      ? `Заявки, созданные за ${period}, — на каком этапе сейчас`
      : 'Все заявки — на каком этапе сейчас',
    dynamics: dyn
      ? `По неделям (МСК), ${day(dyn.from)} — ${day(dyn.to)}${period ? '' : ' (период не выбран: 12 недель)'}`
      : 'По неделям (МСК)',
    workload: 'Открытые заявки на текущую дату; период не применяется',
    programs: period
      ? `Заявки — созданные за ${period}; обучающиеся и потоки — в этом периоде`
      : 'Заявки — за всё время; обучающиеся и потоки — на текущую дату',
    talent: 'Студенты на текущую дату; КАМ — студенты вузов, закреплённых за КАМом',
  };
}

/** Локальный ErrorState (§3.5-паттерн): без стектрейса, requestId мелко. */
function ReportErrorState({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry: () => void;
}): ReactNode {
  const traceId = isApiError(error) ? error.traceId : null;
  return (
    <div className="flex flex-col items-center gap-3 rounded-lg border border-border bg-card px-6 py-10 text-center shadow-card">
      <ErrorBrokenIllustration height={160} />
      <h2 className="text-balance text-base font-semibold">Не удалось загрузить аналитику</h2>
      <p className="text-sm text-muted-foreground">{errorMessage(error)}</p>
      {traceId ? (
        <p className="text-xs text-muted-foreground">Код обращения: {traceId}</p>
      ) : null}
      <Button onClick={onRetry}>Повторить</Button>
    </div>
  );
}

export function DashboardScreen(): ReactNode {
  const navigate = useNavigate();
  const { user, hasPermission, workflowTypesWith } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  // Процессы отчётов — только доступные ролям (F2): один процесс — он и выбран,
  // чужой wf в ссылке игнорируется.
  const reportTypes = useMemo(() => workflowTypesWith('reports:read'), [workflowTypesWith]);
  const rawFilters = useMemo(() => filtersFromParams(searchParams), [searchParams]);
  const filters = useMemo<DashFilters>(() => {
    const allowed = rawFilters.wf && reportTypes.includes(rawFilters.wf) ? rawFilters.wf : null;
    const wf = allowed ?? (reportTypes.length === 1 ? (reportTypes[0] ?? null) : null);
    return wf === rawFilters.wf ? rawFilters : { ...rawFilters, wf };
  }, [rawFilters, reportTypes]);
  const [programsMode, setProgramsMode] = useState<ProgramsMode>('requests');
  const [exporting, setExporting] = useState(false);
  // Чек-лист онбординга §7.5: no-op без провайдера.
  const { completeChecklistItem } = useOnboarding();
  const kamFiltered = filters.kam.length > 0;
  useEffect(() => {
    if (kamFiltered) completeChecklistItem('dashboard-filter');
  }, [kamFiltered, completeChecklistItem]);

  // PNG-геттеры графиков для экспорта «вся панель» (PNG и PDF); порядок — порядок сетки
  const chartPngGetters = useRef(new Map<string, () => string | null>());
  const registerChart = useCallback((id: string, getPng: () => string | null) => {
    chartPngGetters.current.set(id, getPng);
  }, []);

  const patchParams = useCallback(
    (patch: Partial<Record<'from' | 'to' | 'wf', string | null> & Record<ListFilterKey, string[]>>) => {
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

  const params = useMemo(() => reportParams(filters), [filters]);
  const reportQuery = useQuery({
    queryKey: ['dashboard-report', params],
    queryFn: ({ signal }) => getDashboardReport(params, signal),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
  useSseInvalidate('workflow.changed', [['dashboard-report']]);

  const report = reportQuery.data ?? null;
  const loading = reportQuery.isLoading;

  // Справочники фильтров (§6.3): вузы — серверный поиск, остальные целиком.
  // фильтр по КАМам — отчёты по всем КАМам и доступ к списку пользователей
  const canFilterByKam = hasPermission('admin:users') && hasPermission('reports:all');
  const [universitySearch, setUniversitySearch] = useState('');
  const debouncedUniversity = useDebouncedValue(universitySearch, 300);
  const universitiesQuery = useQuery({
    queryKey: ['universities-select', debouncedUniversity],
    queryFn: ({ signal }) => searchUniversities(debouncedUniversity, signal),
    staleTime: 30_000,
  });
  const productsQuery = useQuery({
    queryKey: ['products-select'],
    queryFn: ({ signal }) => listActiveProducts(signal),
    staleTime: 60_000,
  });
  const kamsQuery = useQuery({
    queryKey: ['users-select', 'requests:kam'],
    queryFn: ({ signal }) => listUsers({ permission: 'requests:kam', signal }),
    enabled: canFilterByKam,
    staleTime: 60_000,
  });
  const directionsQuery = useQuery({
    queryKey: ['it-directions', 'all'],
    queryFn: ({ signal }) => listAllItDirections(signal),
    staleTime: 5 * 60_000,
  });
  const programsQuery = useQuery({
    queryKey: ['programs-select'],
    queryFn: ({ signal }) =>
      listPrograms({ limit: 200, offset: 0, sort: 'name', filters: {}, signal }),
    staleTime: 60_000,
  });

  // Подписи выбранных значений для шапки PNG/PDF; вузы — серверный поиск, поэтому
  // имена копятся по мере загрузки результатов
  const labelCache = useRef(new Map<string, string>());
  for (const u of universitiesQuery.data?.items ?? []) labelCache.current.set(u.id, u.name);
  for (const p of productsQuery.data?.items ?? []) labelCache.current.set(p.id, p.name);
  for (const p of programsQuery.data?.items ?? []) labelCache.current.set(p.id, p.name);
  for (const d of directionsQuery.data?.items ?? []) labelCache.current.set(d.id, d.name);
  for (const k of kamsQuery.data?.items ?? []) labelCache.current.set(k.id, k.full_name);
  if (user) labelCache.current.set(user.id, user.full_name ?? user.username ?? 'я');

  const filterLines = useMemo(() => {
    const names = (ids: string[]): string => {
      const known = ids.map((id) => labelCache.current.get(id)).filter((v): v is string => Boolean(v));
      const unknown = ids.length - known.length;
      return [...known, ...(unknown > 0 ? [`ещё ${unknown}`] : [])].join(', ');
    };
    const lines = [
      `Период панели: ${periodText(filters) ?? 'не выбран'}`,
      `Процесс: ${filters.wf ? WORKFLOW_TYPE_LABELS[filters.wf] : 'все процессы роли'}`,
    ];
    const lists: [string, string[]][] = [
      ['КАМ', filters.kam],
      ['Вуз', filters.university],
      ['ИТ-направление', filters.direction],
      ['Продукт', filters.product],
      ['Программа', filters.program],
    ];
    for (const [label, ids] of lists) if (ids.length > 0) lines.push(`${label}: ${names(ids)}`);
    return lines;
  }, [
    filters,
    universitiesQuery.data,
    productsQuery.data,
    programsQuery.data,
    kamsQuery.data,
    directionsQuery.data,
  ]);

  const boardQuery = (extra: Record<string, string>, wf?: string): string => {
    const query = new URLSearchParams();
    query.set('wf', wf ?? filters.wf ?? reportTypes[0] ?? 'b2b');
    // мультифильтр переносится целиком: доска читает все значения (getAll, IN на сервере)
    for (const id of filters.kam) query.append('kam', id);
    for (const id of filters.university) query.append('university', id);
    for (const id of filters.product) query.append('product', id);
    for (const [key, value] of Object.entries(extra)) query.set(key, value);
    return `/board?${query.toString()}`;
  };

  const subtitles = useMemo(() => widgetSubtitles(filters, report), [filters, report]);

  /** Графики панели в порядке сетки — только отрисованные (пустые пропускаются). */
  const collectCharts = (): ChartExport[] =>
    widgets
      .map((widget): ChartExport | null => {
        const getPng = chartPngGetters.current.get(widget.key);
        const dataUrl = widget.hasData && getPng ? getPng() : null;
        return dataUrl
          ? { title: widget.title, subtitle: widget.subtitle, caption: filterLines, dataUrl }
          : null;
      })
      .filter((chart): chart is ChartExport => chart !== null);

  const exportWhole = async (format: 'png' | 'pdf'): Promise<void> => {
    const charts = collectCharts();
    if (charts.length === 0) {
      toastError(new Error('Графики ещё не загружены или пусты'), {
        title: `Экспорт ${format.toUpperCase()} недоступен`,
      });
      return;
    }
    try {
      if (format === 'png') {
        await downloadDashboardPng(charts, filterLines, 'alma-analitika');
        toastSuccess('PNG аналитической панели сохранён');
      } else {
        const kpi = report?.kpi;
        await downloadDashboardPdf(
          {
            title: 'Панель аналитики',
            lines: [
              ...filterLines,
              kpi ? `KPI «Завершено», «Отказ», конверсия — за ${formatKpiPeriod(kpi.period)}` : '',
              'Активные и зависшие — на текущую дату',
            ].filter(Boolean),
            facts: kpi
              ? [
                  { label: 'Заявок активных (сейчас)', value: String(kpi.active.value) },
                  { label: 'Завершено за период', value: String(kpi.completed.value) },
                  { label: 'Отказ за период', value: String(kpi.lost?.value ?? 0) },
                  { label: 'Конверсия', value: `${kpi.conversion.value}%` },
                  { label: 'Зависших (сейчас)', value: String(kpi.stuck.value) },
                  { label: 'Графиков в отчёте', value: String(charts.length) },
                ]
              : [],
          },
          charts,
          'alma-analitika',
        );
        toastSuccess('PDF аналитической панели сохранён', `${charts.length + 1} стр.`);
      }
      completeChecklistItem('export-widget');
    } catch (error) {
      toastError(error, { title: `Не удалось собрать ${format.toUpperCase()}` });
    }
  };

  const exportData = async (format: 'xlsx' | 'csv'): Promise<void> => {
    setExporting(true);
    try {
      const name = await runExportAndDownload(
        {
          entity_type: 'report:dashboard',
          format,
          filters: params as unknown as Record<string, unknown>,
        },
        'crm-dashboard',
      );
      toastSuccess('Файл выгружен', name);
      completeChecklistItem('export-widget');
    } catch (error) {
      toastError(error, { title: 'Не удалось выгрузить данные' });
    } finally {
      setExporting(false);
    }
  };

  const resetPeriodButton = (
    <Button variant="outline" size="sm" onClick={() => patchParams({ from: null, to: null })}>
      Весь период
    </Button>
  );

  const stageSeries = report?.funnel.series ?? [];
  const dynamicsSeries = report?.dynamics.series ?? [];
  const kamRows = report?.kam_workload.rows ?? [];
  const programRows = report?.programs_demand.rows ?? [];
  const autoRanking = report?.programs_demand.auto_ranking ?? false;
  const talentSeries = report?.talent_pool_funnel.series ?? [];
  // «Индекс» — только при включённом авторанжировании; иначе режим откатывается к заявкам
  const programModes = PROGRAM_MODES.filter((mode) => mode.value !== 'demand' || autoRanking);
  const activeProgramsMode: ProgramsMode =
    programsMode === 'demand' && !autoRanking ? 'requests' : programsMode;

  const hasStages = !allZero(stageSeries.map((item) => item.value));
  const hasDynamics = !allZero(dynamicsSeries.flatMap((s) => s.points.map((p) => p.value)));
  const hasWorkload = kamRows.length > 0;
  const hasPrograms =
    programRows.length > 0 &&
    (activeProgramsMode === 'priority' ||
      !allZero(programRows.map((row) => programMetric(row, activeProgramsMode))));
  const hasTalent = !allZero(talentSeries.map((item) => item.value));
  const emptyPeriod = filters.from || filters.to ? 'Нет данных за выбранный период' : 'Нет данных';

  const widgets: Array<{
    key: string;
    title: string;
    subtitle: string;
    hasData: boolean;
    className: string;
    card: ReactNode;
  }> = [
    {
      key: 'stages',
      title: 'Заявки по текущему этапу',
      subtitle: subtitles.stages ?? '',
      hasData: hasStages,
      className: 'col-span-12 lg:col-span-5',
      card: (
        <ChartCard
          chartId="stages"
          title="Заявки по текущему этапу"
          subtitle={subtitles.stages}
          exportCaption={filterLines}
          loading={loading}
          height={stageBarsHeight(stageSeries)}
          empty={!hasStages}
          emptyText={emptyPeriod}
          emptyAction={resetPeriodButton}
          option={hasStages ? stageBarsOption(stageSeries) : null}
          onChartReady={registerChart}
          onSegmentClick={({ dataKey }) => {
            const target = dataKey ? parseStageClickKey(dataKey) : null;
            if (target) navigate(boardQuery({ status: target.status }, target.wf));
          }}
        />
      ),
    },
    {
      key: 'dynamics',
      title: 'Динамика заявок',
      subtitle: subtitles.dynamics ?? '',
      hasData: hasDynamics,
      className: 'col-span-12 lg:col-span-7',
      card: (
        <ChartCard
          chartId="dynamics"
          title="Динамика заявок"
          subtitle={subtitles.dynamics}
          exportCaption={filterLines}
          loading={loading}
          empty={!hasDynamics}
          emptyText={emptyPeriod}
          emptyAction={resetPeriodButton}
          option={hasDynamics ? dynamicsOption(dynamicsSeries) : null}
          onChartReady={registerChart}
        />
      ),
    },
    {
      key: 'kam-workload',
      title: 'Нагрузка по КАМам',
      subtitle: subtitles.workload ?? '',
      hasData: hasWorkload,
      className: 'col-span-12 lg:col-span-6',
      card: (
        <ChartCard
          chartId="kam-workload"
          title="Нагрузка по КАМам"
          subtitle={subtitles.workload}
          exportCaption={filterLines}
          loading={loading}
          height={rowsHeight(kamRows.length, 320)}
          empty={!hasWorkload}
          emptyText="Нет КАМов с заявками под выбранные фильтры"
          option={hasWorkload ? kamWorkloadOption(kamRows) : null}
          onChartReady={registerChart}
          onSegmentClick={({ dataKey }) => {
            if (dataKey) patchParams({ kam: [dataKey] });
          }}
        />
      ),
    },
    {
      key: 'programs',
      title: 'Востребованность программ',
      subtitle: subtitles.programs ?? '',
      hasData: hasPrograms,
      className: 'col-span-12 lg:col-span-6',
      card: (
        <ChartCard
          chartId="programs"
          title="Востребованность программ"
          subtitle={subtitles.programs}
          exportCaption={[
            ...filterLines,
            `Сортировка: ${PROGRAM_MODES.find((m) => m.value === activeProgramsMode)?.label ?? ''}`,
          ]}
          loading={loading}
          height={340}
          empty={!hasPrograms}
          emptyText={emptyPeriod}
          option={hasPrograms ? programsOption(programRows, activeProgramsMode) : null}
          onChartReady={registerChart}
          onSegmentClick={() => navigate('/registry/programs?mode=demand')}
          extra={
            <Segmented
              size="sm"
              aria-label="Показатель востребованности программ"
              value={activeProgramsMode}
              onChange={(value) => setProgramsMode(value as ProgramsMode)}
              options={programModes.map((mode) => ({ label: mode.label, value: mode.value }))}
            />
          }
        />
      ),
    },
    {
      key: 'talent',
      title: 'Пул талантов: студенты по статусам',
      subtitle: subtitles.talent ?? '',
      hasData: hasTalent,
      className: 'col-span-12',
      card: (
        <ChartCard
          chartId="talent"
          title="Пул талантов: студенты по статусам"
          subtitle={subtitles.talent}
          exportCaption={filterLines}
          loading={loading}
          height={260}
          empty={!hasTalent}
          emptyText="Нет студентов под выбранные фильтры"
          option={hasTalent ? talentBarsOption(talentSeries) : null}
          onChartReady={registerChart}
          onSegmentClick={({ dataKey }) => {
            navigate(dataKey ? `/talent-pool?status=${dataKey}` : '/talent-pool');
          }}
        />
      ),
    },
  ];

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-balance text-xl font-semibold leading-7">Аналитика</h1>

      {/* Панель фильтров — одна строка над сеткой (§6.3) */}
      <div data-tour="dashboard-filters" className="flex flex-wrap items-center gap-2">
        <DateRangeFilter
          from={filters.from}
          to={filters.to}
          onChange={(from, to) => patchParams({ from, to })}
        />
        <Segmented
          aria-label="Тип процесса"
          value={filters.wf ?? 'all'}
          onChange={(value) => patchParams({ wf: value === 'all' ? null : value })}
          options={[
            ...reportTypes.map((wf) => ({ label: WORKFLOW_TYPE_LABELS[wf], value: wf })),
            ...(reportTypes.length > 1 ? [{ label: 'Все', value: 'all' }] : []),
          ]}
        />
        {canFilterByKam ? (
          <MultiFilter
            placeholder="КАМ"
            options={(kamsQuery.data?.items ?? []).map((u) => ({
              value: u.id,
              label: u.full_name,
            }))}
            value={filters.kam}
            onChange={(value) => patchParams({ kam: value })}
            loading={kamsQuery.isLoading}
            emptyText="КАМы не найдены"
          />
        ) : hasPermission('requests:kam') ? (
          <Segmented
            aria-label="Мои или все заявки"
            value={filters.kam.length > 0 ? 'mine' : 'all'}
            onChange={(value) => patchParams({ kam: value === 'mine' && user ? [user.id] : [] })}
            options={[
              { label: 'Мои', value: 'mine' },
              { label: 'Все', value: 'all' },
            ]}
          />
        ) : null}
        <MultiFilter
          placeholder="Вуз"
          options={(universitiesQuery.data?.items ?? []).map((u) => ({
            value: u.id,
            label: u.name,
          }))}
          value={filters.university}
          onChange={(value) => patchParams({ university: value })}
          onSearch={setUniversitySearch}
          loading={universitiesQuery.isLoading}
          emptyText="Вузы не найдены"
        />
        <MultiFilter
          placeholder="ИТ-направление"
          options={(directionsQuery.data?.items ?? []).map((d) => ({
            value: d.id,
            label: d.is_active ? d.name : `${d.name} (неактивно)`,
          }))}
          value={filters.direction}
          onChange={(value) => patchParams({ direction: value })}
          loading={directionsQuery.isLoading}
          emptyText="Направления не найдены"
        />
        <MultiFilter
          placeholder="Продукт"
          options={(productsQuery.data?.items ?? []).map((p) => ({
            value: p.id,
            label: p.name,
          }))}
          value={filters.product}
          onChange={(value) => patchParams({ product: value })}
          loading={productsQuery.isLoading}
          emptyText="Продукты не найдены"
        />
        <MultiFilter
          placeholder="Программа"
          options={(programsQuery.data?.items ?? []).map((p) => ({
            value: p.id,
            label: p.name,
          }))}
          value={filters.program}
          onChange={(value) => patchParams({ program: value })}
          loading={programsQuery.isLoading}
          emptyText="Программы не найдены"
        />
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {/* отчёт по КАМам — с теми же фильтрами (период, процесс, КАМы, вузы, направления) */}
          <Button variant="outline" asChild>
            <Link to={kamActivityHref(filters)}>
              Отчёт по КАМам
              <ArrowRight aria-hidden="true" />
            </Link>
          </Button>
          <Button
            variant="ghost"
            onClick={() =>
              patchParams({
                from: null,
                to: null,
                wf: null,
                kam: [],
                university: [],
                direction: [],
                product: [],
                program: [],
              })
            }
          >
            Сбросить
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" disabled={exporting} data-tour="dashboard-export">
                {exporting ? (
                  <LoaderCircle className="animate-spin" aria-hidden="true" />
                ) : (
                  <Download aria-hidden="true" />
                )}
                Экспорт
                <ChevronDown className="text-muted-foreground" aria-hidden="true" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {/* графики — на клиенте: PDF по графику на страницу, PNG одной лентой */}
              <DropdownMenuItem onSelect={() => void exportWhole('pdf')}>
                PDF всей панели
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => void exportWhole('png')}>
                PNG всей панели
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => void exportData('xlsx')}>
                XLSX данные
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => void exportData('csv')}>
                CSV данные
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      {/* Чек-лист «Начало работы» §7.5 — первая позиция сетки, пока не завершён */}
      <ChecklistCard />

      {reportQuery.isError ? (
        <ReportErrorState error={reportQuery.error} onRetry={() => void reportQuery.refetch()} />
      ) : (
        <>
          <KpiCards
            kpi={report?.kpi ?? null}
            loading={loading}
            onStuckClick={() => navigate(boardQuery({ stuck: 'true' }))}
          />
          <motion.div
            variants={staggerContainer}
            initial="hidden"
            animate="visible"
            className="grid grid-cols-12 gap-4"
          >
            {widgets.map((widget) => (
              <motion.div key={widget.key} variants={staggerItem} className={widget.className}>
                {widget.card}
              </motion.div>
            ))}
          </motion.div>
        </>
      )}
    </div>
  );
}

export default DashboardScreen;
