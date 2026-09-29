import { ChartLine, CircleCheck, Flame, Percent } from 'lucide-react';
import { motion } from 'motion/react';
import type { ReactNode } from 'react';
import type { DashboardKpi, DashboardKpiValue } from '@/shared/api/types';
import { staggerContainer, staggerItem } from '@/shared/lib/motion';
import { StatCard } from '@/shared/ui/stat-card';

/**
 * KPI-ряд дашборда (redesign.md §6.3): 4 StatCard с count-up и дельтой,
 * stagger-вход; тона primary/success/primary/warning («Зависших» — Flame,
 * клик — drill-down на канбан с фильтром stuck=true, ux.md §6.1).
 */

interface KpiCardsProps {
  kpi: DashboardKpi | null;
  loading: boolean;
  onStuckClick: () => void;
}

function deltaOf(value: DashboardKpiValue | undefined): number | undefined {
  const delta = value?.delta;
  return delta === null || delta === undefined || delta === 0 ? undefined : delta;
}

export function KpiCards({ kpi, loading, onStuckClick }: KpiCardsProps): ReactNode {
  const pending = loading || !kpi;
  return (
    <motion.div
      variants={staggerContainer}
      initial="hidden"
      animate="visible"
      className="grid grid-cols-2 gap-4 xl:grid-cols-4"
    >
      <motion.div variants={staggerItem}>
        <StatCard
          title="Заявок активных"
          icon={ChartLine}
          tone="primary"
          value={kpi?.active.value ?? 0}
          delta={deltaOf(kpi?.active)}
          loading={pending}
        />
      </motion.div>
      <motion.div variants={staggerItem}>
        <StatCard
          title="Завершено за период"
          icon={CircleCheck}
          tone="success"
          value={kpi?.completed.value ?? 0}
          delta={deltaOf(kpi?.completed)}
          loading={pending}
        />
      </motion.div>
      <motion.div variants={staggerItem}>
        <StatCard
          title="Конверсия"
          icon={Percent}
          tone="primary"
          value={kpi?.conversion.value ?? 0}
          suffix="%"
          delta={deltaOf(kpi?.conversion)}
          loading={pending}
        />
      </motion.div>
      <motion.div variants={staggerItem}>
        <StatCard
          title="Зависших"
          icon={Flame}
          tone="warning"
          value={kpi?.stuck.value ?? 0}
          delta={deltaOf(kpi?.stuck)}
          invertedDelta
          hint="Заявки без движения дольше порога. Открыть на доске"
          onClick={onStuckClick}
          loading={pending}
        />
      </motion.div>
    </motion.div>
  );
}
