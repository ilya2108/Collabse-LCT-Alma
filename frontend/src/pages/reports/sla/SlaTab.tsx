import { useQuery } from '@tanstack/react-query';
import { ArrowLeft, CalendarClock, ChevronDown, Download, LoaderCircle } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { KamActivityParams } from '@/shared/api/endpoints/kamActivity';
import {
  exportSla,
  getSla,
  getSlaKam,
  type SlaComparison,
  type SlaExportFormat,
  type SlaExportOptions,
  type SlaPeriod,
} from '@/shared/api/endpoints/sla';
import { isApiError } from '@/shared/api/errors';
import { useAuth } from '@/shared/auth/AuthContext';
import { toastError, toastSuccess } from '@/shared/lib/toast';
import { useBreakpoint } from '@/shared/lib/useBreakpoint';
import { Button } from '@/shared/ui/button';
import { ErrorState } from '@/shared/ui/data-table';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/shared/ui/dropdown-menu';
import { AiSummaryButton } from '../ai/AiSummaryButton';
import { AiSummaryPanel } from '../ai/AiSummaryPanel';
import { useAiSummary } from '../ai/useAiSummary';
import { SlaHowItWorks } from './SlaHowItWorks';
import { SlaKamView } from './SlaKamView';
import { SlaTeamView } from './SlaTeamView';
import { isTrendMetric, type SlaTrendMetric } from './slaCharts';
import { mskDateTime, mskDay } from './slaFormat';

/**
 * Вкладка «Просрочки» отчёта «Активность КАМов» (api-contract.md §7.2.2): просроченные
 * этапы заявок по КАМам — для руководства агрегат по команде, для каждого КАМа —
 * подробный разбор. Принимает те же фильтры, что остальные вкладки (`params` из
 * `toApiParams`: период, процесс, КАМы, вузы, ИТ-направления). Своё URL-состояние:
 * `sla_kam` — открытый разбор КАМа, `sla_metric` — показатель тренда. При «только свои»
 * сразу открывается разбор себя. Карточка заявки — через `onOpenRequest` (`request=`).
 * Своя кнопка «Экспорт» (XLSX, PDF без имён / по КАМам, CSV просроченных). В разборе
 * КАМа — кнопка и панель «Сводка ИИ» по этому КАМу; при «только свои» её заменяет
 * кнопка в шапке страницы (сводка по себе), чтобы не было двух одинаковых.
 */

export interface SlaTabProps {
  params: KamActivityParams;
  /** Нет права «все КАМы» — только свои заявки. */
  ownOnly: boolean;
  onOpenRequest: (id: string) => void;
}

const KAM_PARAM = 'sla_kam';
const METRIC_PARAM = 'sla_metric';

interface ExportChoice {
  key: string;
  label: string;
  format: SlaExportFormat;
  options?: SlaExportOptions;
}

/** «Данные на 16.10.2026 14:05 МСК · остатки — к 01.10, за период — к 15.09–30.09». */
function dataLine(period: SlaPeriod, comparison: SlaComparison): string {
  const asOf = period.is_current ? `${mskDateTime(period.as_of)} МСК` : mskDay(period.to);
  return `Данные на ${asOf} · остатки — ${comparison.snapshot_label}, за период — ${comparison.period_label}`;
}

/** Доска с фильтром «Зависшие» в той же области (процесс, КАМы) — только для текущего периода. */
function boardLinkFor(params: KamActivityParams, kamIds: string[] | undefined): string {
  const query = new URLSearchParams({ stuck: 'true' });
  if (params.workflow_type) query.set('wf', params.workflow_type);
  for (const id of kamIds ?? []) query.append('kam', id);
  return `/board?${query.toString()}`;
}

export function SlaTab({ params, ownOnly, onOpenRequest }: SlaTabProps): ReactNode {
  const { user } = useAuth();
  const { md } = useBreakpoint();
  const compact = !md;
  const [searchParams, setSearchParams] = useSearchParams();
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [exporting, setExporting] = useState<string | null>(null);

  const urlKam = searchParams.get(KAM_PARAM);
  const metricRaw = searchParams.get(METRIC_PARAM);
  const metric: SlaTrendMetric = isTrendMetric(metricRaw) ? metricRaw : 'overdue';
  // «только свои» — сразу разбор себя; иначе — КАМ из URL или командный вид
  const kamId: string | null = ownOnly ? (user?.id ?? null) : urlKam;

  const setParam = useCallback(
    (key: string, value: string | null, push = false) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (value) next.set(key, value);
          else next.delete(key);
          return next;
        },
        { replace: !push },
      );
    },
    [setSearchParams],
  );

  const selectKam = useCallback((id: string) => setParam(KAM_PARAM, id, true), [setParam]);
  const backToTeam = useCallback(() => setParam(KAM_PARAM, null, true), [setParam]);
  const setMetric = useCallback(
    (value: SlaTrendMetric) => setParam(METRIC_PARAM, value === 'overdue' ? null : value),
    [setParam],
  );

  // при переходе «команда ↔ КАМ» — к началу вкладки
  useEffect(() => {
    rootRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [kamId]);

  const teamQuery = useQuery({
    queryKey: ['sla', params],
    queryFn: ({ signal }) => getSla(params, signal),
    staleTime: 30_000,
    enabled: kamId === null,
  });
  const kamQuery = useQuery({
    queryKey: ['sla-kam', kamId, params],
    queryFn: ({ signal }) => getSlaKam(kamId ?? '', params, signal),
    staleTime: 30_000,
    enabled: kamId !== null,
  });

  const aiKam = useAiSummary({
    kind: 'kam',
    kamId,
    params,
    enabled: kamId !== null && !ownOnly,
    kamName: kamQuery.data?.kam.name ?? null,
  });

  const active = kamId === null ? teamQuery : kamQuery;
  const report = kamId === null ? teamQuery.data : kamQuery.data;
  const period = report?.period ?? null;
  const comparison = report?.comparison ?? null;

  const caption = useCallback(
    (source: string): string =>
      period ? `${mskDay(period.from)} — ${mskDay(period.to)} · Источник: ${source}` : '',
    [period],
  );
  const exportCaption = useMemo(
    () =>
      period && comparison
        ? [
            `Период: ${mskDay(period.from)} — ${mskDay(period.to)}`,
            dataLine(period, comparison),
            ...(kamQuery.data && kamId !== null ? [`КАМ: ${kamQuery.data.kam.name}`] : []),
          ]
        : [],
    [period, comparison, kamQuery.data, kamId],
  );

  const exportParams: KamActivityParams = useMemo(
    () => (kamId !== null && !ownOnly ? { ...params, kam_id: [kamId] } : params),
    [kamId, ownOnly, params],
  );
  const choices: ExportChoice[] =
    ownOnly || kamId !== null
      ? [
          { key: 'xlsx', label: 'XLSX — все листы', format: 'xlsx' },
          { key: 'pdf', label: ownOnly ? 'PDF — мои просрочки' : 'PDF — разбор КАМа', format: 'pdf', options: { by_kam: true } },
          { key: 'csv', label: 'CSV — просроченные заявки', format: 'csv' },
        ]
      : [
          { key: 'xlsx', label: 'XLSX — все листы', format: 'xlsx' },
          { key: 'pdf-summary', label: 'PDF — сводка без имён', format: 'pdf', options: { by_kam: false } },
          { key: 'pdf-kams', label: 'PDF — по КАМам', format: 'pdf', options: { by_kam: true } },
          { key: 'csv', label: 'CSV — просроченные заявки', format: 'csv' },
        ];

  const runExport = async (choice: ExportChoice): Promise<void> => {
    setExporting(choice.key);
    try {
      const name = await exportSla(choice.format, exportParams, choice.options);
      toastSuccess('Файл выгружен', name);
    } catch (error) {
      toastError(error, { title: 'Не удалось выгрузить отчёт' });
    } finally {
      setExporting(null);
    }
  };

  const exportMenu = (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" disabled={exporting !== null}>
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
        {choices.map((choice) => (
          <DropdownMenuItem key={choice.key} onSelect={() => void runExport(choice)}>
            {choice.label}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );

  const header = (
    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-sm text-muted-foreground">
        <span className="min-w-0 text-pretty">
          <CalendarClock className="mr-1.5 inline size-4 align-[-3px]" aria-hidden="true" />
          {period && comparison ? dataLine(period, comparison) : 'Данные загружаются…'}
        </span>
        <SlaHowItWorks definitions={report?.definitions} />
      </div>
      {exportMenu}
    </div>
  );

  let body: ReactNode;
  if (active.isError) {
    const error = active.error;
    const status = isApiError(error) ? error.status : null;
    body = (
      <div className="flex flex-col items-center gap-2">
        <ErrorState
          error={error}
          title={
            status === 404
              ? 'КАМ не найден'
              : status === 403
                ? 'Нет доступа к этому разбору'
                : 'Не удалось загрузить просрочки'
          }
          onRetry={status === 403 || status === 404 ? undefined : () => void active.refetch()}
        />
        {kamId !== null && !ownOnly ? (
          <Button variant="outline" onClick={backToTeam}>
            <ArrowLeft aria-hidden="true" />
            Все КАМы
          </Button>
        ) : null}
      </div>
    );
  } else if (kamId !== null) {
    body = (
      <SlaKamView
        report={kamQuery.data ?? null}
        loading={kamQuery.isLoading}
        compact={compact}
        onBack={ownOnly ? undefined : backToTeam}
        metric={metric}
        onMetricChange={setMetric}
        onOpenRequest={onOpenRequest}
        boardLink={kamQuery.data?.period.is_current ? boardLinkFor(params, [kamId]) : null}
        caption={caption}
        exportCaption={exportCaption}
        headerExtra={aiKam.available ? <AiSummaryButton controller={aiKam} size="sm" /> : undefined}
        aiPanel={aiKam.open ? <AiSummaryPanel controller={aiKam} /> : undefined}
      />
    );
  } else {
    body = (
      <SlaTeamView
        report={teamQuery.data ?? null}
        loading={teamQuery.isLoading}
        compact={compact}
        metric={metric}
        onMetricChange={setMetric}
        onSelectKam={selectKam}
        onOpenRequest={onOpenRequest}
        boardLink={teamQuery.data?.period.is_current ? boardLinkFor(params, params.kam_id) : null}
        caption={caption}
        exportCaption={exportCaption}
      />
    );
  }

  return (
    <div ref={rootRef} className="flex min-w-0 scroll-mt-4 flex-col gap-4">
      {header}
      {body}
    </div>
  );
}

export default SlaTab;
