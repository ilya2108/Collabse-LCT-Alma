import type { ReactNode } from 'react';
import type { SlaKamRow } from '@/shared/api/endpoints/sla';
import { ChartCard } from '../../dashboard/ChartCard';
import { SOURCE_SLA_LOAD, loadScatterOption } from './slaCharts';

/**
 * «Нагрузка и просрочки»: КАМ — точка (X — в работе, Y — просрочено, шт.), пунктир —
 * медианы команды. Отвечает на вопрос «не из-за нагрузки ли»: видно, у кого просрочек
 * больше при той же нагрузке. Имена подписаны только у КАМов из списка по правилу;
 * подсказка — в работе, просрочено, из них при передаче, поступило за период. Клик
 * открывает разбор КАМа (у совпадающих точек — список имён в подсказке).
 */

interface SlaLoadScatterProps {
  kams: SlaKamRow[];
  activeMedian: number | null;
  overdueMedian: number | null;
  caption: (source: string) => string;
  exportCaption?: string[];
  compact: boolean;
  loading?: boolean;
  onSelectKam: (kamId: string) => void;
}

export function SlaLoadScatter({
  kams,
  activeMedian,
  overdueMedian,
  caption,
  exportCaption,
  compact,
  loading = false,
  onSelectKam,
}: SlaLoadScatterProps): ReactNode {
  const rows = kams.filter((row) => row.active > 0 || row.overdue > 0);
  return (
    <ChartCard
      chartId="sla-load"
      title="Нагрузка и просрочки"
      subtitle="Каждая точка — КАМ на дату среза; пунктир — медианы по команде. Подписаны КАМы из списка по правилу."
      exportCaption={exportCaption}
      loading={loading}
      height={compact ? 260 : 320}
      empty={rows.length === 0}
      emptyText="Заявок в работе нет"
      option={
        rows.length > 0
          ? loadScatterOption(
              rows,
              { active: activeMedian, overdue: overdueMedian },
              caption(SOURCE_SLA_LOAD),
              compact,
            )
          : null
      }
      onSegmentClick={({ dataKey }) => {
        if (dataKey) onSelectKam(dataKey);
      }}
    />
  );
}
