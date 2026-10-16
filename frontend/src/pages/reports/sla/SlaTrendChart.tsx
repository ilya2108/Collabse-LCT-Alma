import type { ReactNode } from 'react';
import type { SlaTrend } from '@/shared/api/endpoints/sla';
import { ChartCard } from '../../dashboard/ChartCard';
import { Segmented } from '../../dashboard/Segmented';
import { SOURCE_SLA, TREND_METRICS, trendOption, type SlaTrendMetric } from './slaCharts';
import { trendCaption } from './slaFormat';

/**
 * Тренд просрочек по срезам (неделя — на конец воскресенья МСК, не короче 8 недель;
 * длинный период — по месяцам): «Просрочено» (со светлой областью «в работе»),
 * «Больше 30 дн.», «Уложились в норму, %» (при решённых меньше 5 доля не считается —
 * в подсказке только количество). Период отчёта выделен, незаконченная точка — пунктиром.
 */

interface SlaTrendChartProps {
  trend: SlaTrend | null;
  metric: SlaTrendMetric;
  onMetricChange: (metric: SlaTrendMetric) => void;
  caption: (source: string) => string;
  exportCaption?: string[];
  compact: boolean;
  loading?: boolean;
  /** Заголовок: «Тренд» или «Тренд КАМа». */
  title?: string;
  chartId?: string;
}

export function SlaTrendChart({
  trend,
  metric,
  onMetricChange,
  caption,
  exportCaption,
  compact,
  loading = false,
  title = 'Тренд',
  chartId = 'sla-trend',
}: SlaTrendChartProps): ReactNode {
  const points = trend?.points ?? [];
  const empty =
    points.length === 0 ||
    points.every((point) =>
      metric === 'on_time' ? point.decided === 0 : point.active === 0 && point.overdue === 0,
    );
  const incomplete = points.length > 0 && points[points.length - 1]?.complete === false;
  const subtitle = trend
    ? `${trendCaption(trend.granularity)}${
        incomplete
          ? ` · последняя точка пунктиром — ${trend.granularity === 'month' ? 'месяц' : 'неделя'} не закончил${
              trend.granularity === 'month' ? 'ся' : 'ась'
            }`
          : ''
      }`
    : undefined;
  return (
    <ChartCard
      chartId={chartId}
      title={title}
      subtitle={subtitle}
      exportCaption={exportCaption}
      loading={loading}
      height={compact ? 220 : 280}
      empty={empty}
      emptyText={metric === 'on_time' ? 'Решённых этапов в окне тренда нет' : 'Заявок в работе в окне тренда нет'}
      option={trend && !empty ? trendOption(trend, metric, caption(SOURCE_SLA), compact) : null}
      extra={
        <Segmented
          size="sm"
          aria-label="Показатель тренда"
          value={metric}
          onChange={(value) => onMetricChange(value as SlaTrendMetric)}
          options={TREND_METRICS}
        />
      }
    />
  );
}
