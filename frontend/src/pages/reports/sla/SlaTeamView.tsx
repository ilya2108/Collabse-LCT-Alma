import type { ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import type { SlaReport } from '@/shared/api/endpoints/sla';
import { EmptyState } from '@/shared/ui/data-table';
import { SlaAttention } from './SlaAttention';
import { SlaHeadline } from './SlaHeadline';
import { SlaNotes } from './SlaHowItWorks';
import { SlaKamTable } from './SlaKamTable';
import { SlaKpiRow } from './SlaKpiRow';
import { SlaLoadScatter } from './SlaLoadScatter';
import { SlaStagesCard } from './SlaStagesCard';
import { SlaTrendChart } from './SlaTrendChart';
import type { SlaTrendMetric } from './slaCharts';

/**
 * Командный вид «Просрочек» (для права «все КАМы»): итог одной строкой → определение →
 * 4 KPI → «Где застревают» и «Просрочек больше, чем по команде» → «Нагрузка и просрочки»
 * и тренд → таблица КАМов → оговорки о данных. Агрегат для руководства сверху,
 * индивидуальный разбор — по клику на КАМа.
 */

interface SlaTeamViewProps {
  report: SlaReport | null;
  loading: boolean;
  compact: boolean;
  metric: SlaTrendMetric;
  onMetricChange: (metric: SlaTrendMetric) => void;
  onSelectKam: (kamId: string) => void;
  onOpenRequest: (id: string) => void;
  /** Доска с фильтром «Зависшие» в той же области; null — период не текущий. */
  boardLink: string | null;
  caption: (source: string) => string;
  exportCaption: string[];
}

export function SlaTeamView({
  report,
  loading,
  compact,
  metric,
  onMetricChange,
  onSelectKam,
  onOpenRequest,
  boardLink,
  caption,
  exportCaption,
}: SlaTeamViewProps): ReactNode {
  const navigate = useNavigate();
  const pending = loading || !report;

  const active = report?.kpi.active?.value ?? 0;
  const decided = report?.kpi.decided?.value ?? 0;
  const trendEmpty = (report?.trend.points ?? []).every((point) => point.active === 0 && point.decided === 0);
  if (report && active === 0 && decided === 0 && trendEmpty) {
    return (
      <div className="flex min-w-0 flex-col gap-4">
        <SlaHeadline headline={report.headline} definition={report.definition} />
        <EmptyState
          title="Заявок в работе нет"
          description="В выбранном периоде и области нет заявок на нефинальных этапах и решённых этапов. Попробуйте другой период или снимите фильтры."
        />
        <SlaNotes notes={report.notes} />
      </div>
    );
  }

  const matched = report ? report.kams.filter((row) => row.attention).length : 0;

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <SlaHeadline
        headline={report?.headline ?? null}
        definition={report?.definition ?? null}
        loading={pending}
      />
      <SlaKpiRow
        kpi={report?.kpi ?? null}
        comparison={report?.comparison ?? null}
        loading={pending}
        onOverdueClick={boardLink ? () => navigate(boardLink) : undefined}
      />

      <div className="grid grid-cols-12 items-stretch gap-4">
        <div className="col-span-12 min-w-0 lg:col-span-7">
          <SlaStagesCard
            stages={report?.stages ?? {}}
            byWorkflow={report?.by_workflow ?? {}}
            caption={caption}
            exportCaption={exportCaption}
            isCurrent={report?.period.is_current ?? false}
            compact={compact}
            loading={pending}
          />
        </div>
        <div className="col-span-12 min-w-0 lg:col-span-5">
          <SlaAttention
            attention={report?.attention ?? null}
            matched={matched}
            onSelectKam={onSelectKam}
            loading={pending}
          />
        </div>
      </div>

      <div className="grid grid-cols-12 items-start gap-4">
        <div className="col-span-12 min-w-0 xl:col-span-5">
          <SlaLoadScatter
            kams={report?.kams ?? []}
            activeMedian={report?.team.active_median ?? null}
            overdueMedian={report?.team.overdue_median ?? null}
            caption={caption}
            exportCaption={exportCaption}
            compact={compact}
            loading={pending}
            onSelectKam={onSelectKam}
          />
        </div>
        <div className="col-span-12 min-w-0 xl:col-span-7">
          <SlaTrendChart
            trend={report?.trend ?? null}
            metric={metric}
            onMetricChange={onMetricChange}
            caption={caption}
            exportCaption={exportCaption}
            compact={compact}
            loading={pending}
          />
        </div>
      </div>

      {report ? (
        <SlaKamTable
          kams={report.kams}
          overdueItems={report.overdue.items}
          activeMedian={report.team.active_median ?? null}
          inactiveOwners={report.inactive_owners}
          snapshotLabel={report.comparison.snapshot_label}
          compact={compact}
          onSelectKam={onSelectKam}
          onOpenRequest={onOpenRequest}
        />
      ) : (
        <div className="shimmer h-64 rounded-lg" aria-hidden="true" />
      )}

      {report?.overdue.truncated ? (
        <p className="text-xs text-muted-foreground">
          Просроченных заявок {report.overdue.total}: в раскрытии строк показаны первые{' '}
          {report.overdue.items.length}, полный список — в выгрузке XLSX.
        </p>
      ) : null}

      {report ? <SlaNotes notes={report.notes} /> : null}
    </div>
  );
}
