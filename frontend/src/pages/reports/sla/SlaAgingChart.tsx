import type { ReactNode } from 'react';
import type { SlaAgingRow } from '@/shared/api/endpoints/sla';
import { ChartCard } from '../../dashboard/ChartCard';
import { SOURCE_SLA, agingOption } from './slaCharts';

/**
 * «Старение» в разборе КАМа: просроченные заявки по корзинам задержки сверх нормы
 * (1–3, 4–7, 8–14, 15–30, больше 30 дней) на дату среза; контуром — на начало периода.
 */

interface SlaAgingChartProps {
  aging: SlaAgingRow[];
  snapshotLabel: string;
  caption: (source: string) => string;
  exportCaption?: string[];
  compact: boolean;
  loading?: boolean;
}

export function SlaAgingChart({
  aging,
  snapshotLabel,
  caption,
  exportCaption,
  compact,
  loading = false,
}: SlaAgingChartProps): ReactNode {
  const empty = aging.every((row) => row.count === 0 && (row.prev ?? 0) === 0);
  // «к 01.10» → «на 01.10»: контур — срез на начало периода
  const previous = snapshotLabel.replace(/^к\s+/, 'на ');
  return (
    <ChartCard
      chartId="sla-kam-aging"
      title="Старение"
      subtitle="Просроченные заявки по задержке сверх нормы на дату среза"
      exportCaption={exportCaption}
      loading={loading}
      height={compact ? 220 : 260}
      empty={empty}
      emptyText="Просроченных заявок нет"
      option={
        empty
          ? null
          : agingOption(aging, caption(SOURCE_SLA), { current: 'Сейчас', previous }, compact)
      }
    />
  );
}
