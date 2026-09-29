import { useQuery } from '@tanstack/react-query';
import { ChevronDown, Download, LoaderCircle } from 'lucide-react';
import { motion } from 'motion/react';
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { ErrorBrokenIllustration } from '@/app/localIllustrations';
import { listActiveProducts } from '@/shared/api/endpoints/products';
import { runExportAndDownload } from '@/shared/api/endpoints/exportJobs';
import { getDashboardReport, type DashboardReportParams } from '@/shared/api/endpoints/reports';
import { searchUniversities } from '@/shared/api/endpoints/universities';
import { listUsers } from '@/shared/api/endpoints/users';
import { errorMessage, isApiError } from '@/shared/api/errors';
import { useAuth } from '@/shared/auth/AuthContext';
import { dayjs } from '@/shared/lib/dayjs';
import { staggerContainer, staggerItem } from '@/shared/lib/motion';
import { toastError, toastSuccess } from '@/shared/lib/toast';
import { useDebouncedValue } from '@/shared/lib/useDebouncedValue';
import { useFlags } from '@/shared/lib/useFlags';
import { useSseInvalidate } from '@/shared/lib/useSseInvalidate';
import { Button } from '@/shared/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/shared/ui/dropdown-menu';
import { ChartCard } from './ChartCard';
import { DateRangeFilter, MultiFilter } from './FilterControls';
import { ChecklistCard, useOnboarding } from '@/features/onboarding';
import { KpiCards } from './KpiCards';
import {
  composeDashboardPng,
  dynamicsOption,
  funnelOption,
  kamWorkloadOption,
  programsOption,
  talentFunnelOption,
  type ProgramsMode,
} from './model';
import { Segmented } from './Segmented';

/**
 * Дашборд (redesign.md §6.3, ux.md §6): панель фильтров одной строкой над
 * сеткой (состояние в URL — ссылку можно переслать), KPI-ряд StatCard,
 * виджеты ECharts на теме 'crm' в сетке 12 колонок со stagger-входом,
 * drill-down кликами и экспорт PNG (клиент) / XLSX-CSV (через /export/jobs).
 */

interface DashFilters {
  from: string | null;
  to: string | null;
  wf: 'b2b' | 'b2c' | null;
  kam: string[];
  university: string[];
  product: string[];
}

function filtersFromParams(params: URLSearchParams): DashFilters {
  const wfRaw = params.get('wf');
  return {
    from: params.get('from'),
    to: params.get('to'),
    wf: wfRaw === 'b2b' || wfRaw === 'b2c' ? wfRaw : null,
    kam: params.getAll('kam'),
    university: params.getAll('university'),
    product: params.getAll('product'),
  };
}

function reportParams(f: DashFilters): DashboardReportParams {
  return {
    workflow_type: f.wf ?? undefined,
    from: f.from ?? undefined,
    to: f.to ?? undefined,
    kam_id: f.kam.length > 0 ? f.kam : undefined,
    university_id: f.university.length > 0 ? f.university : undefined,
    product_id: f.product.length > 0 ? f.product : undefined,
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
  const { user, hasRole } = useAuth();
  const { flags } = useFlags();
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = useMemo(() => filtersFromParams(searchParams), [searchParams]);
  const [programsMode, setProgramsMode] = useState<ProgramsMode>('requests');
  const [exporting, setExporting] = useState(false);
  // Чек-лист онбординга §7.5: no-op без провайдера.
  const { completeChecklistItem } = useOnboarding();
  const kamFiltered = filters.kam.length > 0;
  useEffect(() => {
    if (kamFiltered) completeChecklistItem('dashboard-filter');
  }, [kamFiltered, completeChecklistItem]);

  // PNG-геттеры графиков для композитного экспорта «весь дашборд»
  const chartPngGetters = useRef(new Map<string, { title: string; getPng: () => string | null }>());
  const registerChart = useCallback(
    (title: string) => (id: string, getPng: () => string | null) => {
      chartPngGetters.current.set(id, { title, getPng });
    },
    [],
  );

  const patchParams = useCallback(
    (patch: Partial<Record<'from' | 'to' | 'wf', string | null> & Record<'kam' | 'university' | 'product', string[]>>) => {
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
  const canFilterByKam = hasRole('admin', 'head_kam');
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
    queryKey: ['users-select', 'kam'],
    queryFn: ({ signal }) => listUsers({ role: 'kam', signal }),
    enabled: canFilterByKam,
    staleTime: 60_000,
  });

  const boardQuery = (extra: Record<string, string>): string => {
    const query = new URLSearchParams();
    query.set('wf', filters.wf === 'b2c' ? 'b2c' : 'b2b');
    if (filters.kam[0]) query.set('kam', filters.kam[0]);
    if (filters.university[0]) query.set('university', filters.university[0]);
    if (filters.product[0]) query.set('product', filters.product[0]);
    for (const [key, value] of Object.entries(extra)) query.set(key, value);
    return `/board?${query.toString()}`;
  };

  const captionText = useMemo(() => {
    const parts: string[] = [];
    parts.push(
      filters.from || filters.to
        ? `Период: ${filters.from ?? '…'} — ${filters.to ?? '…'}`
        : 'Период: всё время',
    );
    parts.push(`Тип: ${filters.wf === 'b2b' ? 'B2B' : filters.wf === 'b2c' ? 'B2C' : 'все'}`);
    parts.push(dayjs().format('D MMM YYYY, HH:mm'));
    return parts.join(' · ');
  }, [filters]);

  const exportWholePng = async (): Promise<void> => {
    const charts = [...chartPngGetters.current.values()]
      .map(({ title, getPng }) => ({ title, dataUrl: getPng() }))
      .filter((c): c is { title: string; dataUrl: string } => Boolean(c.dataUrl));
    if (charts.length === 0) {
      toastError(new Error('Графики ещё не загружены'), { title: 'Экспорт PNG недоступен' });
      return;
    }
    try {
      await composeDashboardPng(charts, captionText);
      toastSuccess('PNG аналитической панели сохранён');
      completeChecklistItem('export-widget');
    } catch (error) {
      toastError(error, { title: 'Не удалось собрать PNG' });
    }
  };

  const exportData = async (format: 'xlsx' | 'csv' | 'pdf'): Promise<void> => {
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

  const funnelSeries = report?.funnel.series ?? [];
  const dynamicsSeries = report?.dynamics.series ?? [];
  const kamRows = report?.kam_workload.rows ?? [];
  const programRows = report?.programs_demand.rows ?? [];
  const talentSeries = report?.talent_pool_funnel.series ?? [];

  const widgets: Array<{ key: string; className: string; card: ReactNode }> = [
    {
      key: 'funnel',
      className: 'col-span-12 lg:col-span-5',
      card: (
        <ChartCard
          chartId="funnel"
          title="Воронка по этапам"
          loading={loading}
          empty={funnelSeries.length === 0}
          emptyAction={resetPeriodButton}
          option={funnelSeries.length > 0 ? funnelOption(funnelSeries) : null}
          onChartReady={registerChart('Воронка по этапам')}
          onSegmentClick={({ dataKey }) => {
            if (dataKey) navigate(boardQuery({ status: dataKey }));
          }}
        />
      ),
    },
    {
      key: 'dynamics',
      className: 'col-span-12 lg:col-span-7',
      card: (
        <ChartCard
          chartId="dynamics"
          title="Динамика заявок"
          loading={loading}
          empty={dynamicsSeries.every((s) => s.points.length === 0)}
          emptyAction={resetPeriodButton}
          option={dynamicsSeries.length > 0 ? dynamicsOption(dynamicsSeries) : null}
          onChartReady={registerChart('Динамика заявок')}
        />
      ),
    },
    {
      key: 'kam-workload',
      className: 'col-span-12 lg:col-span-6',
      card: (
        <ChartCard
          chartId="kam-workload"
          title="Нагрузка по КАМам"
          loading={loading}
          empty={kamRows.length === 0}
          option={kamRows.length > 0 ? kamWorkloadOption(kamRows) : null}
          onChartReady={registerChart('Нагрузка по КАМам')}
          onSegmentClick={({ dataKey }) => {
            if (dataKey) patchParams({ kam: [dataKey] });
          }}
        />
      ),
    },
    {
      key: 'programs',
      className: 'col-span-12 lg:col-span-6',
      card: (
        <ChartCard
          chartId="programs"
          title="Топ программ"
          loading={loading}
          empty={programRows.length === 0}
          option={programRows.length > 0 ? programsOption(programRows, programsMode) : null}
          onChartReady={registerChart('Топ программ')}
          onSegmentClick={() => navigate('/registry/programs')}
          extra={
            <Segmented
              size="sm"
              aria-label="Режим сортировки программ"
              value={programsMode}
              onChange={(value) => setProgramsMode(value as ProgramsMode)}
              options={[
                { label: 'По заявкам', value: 'requests' },
                { label: 'По приоритету', value: 'priority' },
              ]}
            />
          }
        />
      ),
    },
    {
      key: 'talent-funnel',
      className: 'col-span-12',
      card: (
        <ChartCard
          chartId="talent-funnel"
          title="Пул талантов: воронка студентов"
          loading={loading}
          empty={talentSeries.length === 0}
          option={talentSeries.length > 0 ? talentFunnelOption(talentSeries) : null}
          onChartReady={registerChart('Воронка студентов')}
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
            { label: 'B2B', value: 'b2b' },
            { label: 'B2C', value: 'b2c' },
            { label: 'Все', value: 'all' },
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
        ) : hasRole('kam') ? (
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
        <div className="ml-auto flex items-center gap-2">
          <Button
            variant="ghost"
            onClick={() =>
              patchParams({ from: null, to: null, wf: null, kam: [], university: [], product: [] })
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
              <DropdownMenuItem onSelect={() => void exportWholePng()}>
                PNG всей панели
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => void exportData('xlsx')}>
                XLSX данные
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => void exportData('csv')}>
                CSV данные
              </DropdownMenuItem>
              {flags.report_pdf ? (
                <DropdownMenuItem onSelect={() => void exportData('pdf')}>
                  PDF отчёт
                </DropdownMenuItem>
              ) : null}
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
