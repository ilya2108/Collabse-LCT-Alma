import { ChartLine, CircleCheck, Flame, Percent } from 'lucide-react';
import { motion } from 'motion/react';
import type { ReactNode } from 'react';
import type { DashboardKpiBlock, DashboardKpiPeriod } from '@/shared/api/endpoints/reports';
import type { DashboardKpiValue } from '@/shared/api/types';
import { dayjs } from '@/shared/lib/dayjs';
import { staggerContainer, staggerItem } from '@/shared/lib/motion';
import { StatCard } from '@/shared/ui/stat-card';

/**
 * KPI-ряд дашборда (redesign.md §6.3): 4 StatCard с count-up и дельтой,
 * stagger-вход; тона primary/success/primary/warning («Зависших» — Flame,
 * клик — drill-down на канбан с фильтром stuck=true, ux.md §6.1).
 *
 * Под рядом — период, за который посчитаны «Завершено» и конверсия (без выбранного
 * периода — последние 30 дней, и это написано), и что «активные» и «зависшие» — срез
 * на текущую дату. «Завершено» — вход в этап «Завершена» по истории переходов, тот же
 * состав, что у линии «Завершено» в динамике; «Отказ» — отдельно.
 */

interface KpiCardsProps {
  kpi: DashboardKpiBlock | null;
  loading: boolean;
  onStuckClick: () => void;
}

function deltaOf(value: DashboardKpiValue | undefined): number | undefined {
  const delta = value?.delta;
  return delta === null || delta === undefined || delta === 0 ? undefined : delta;
}

function day(iso: string): string {
  return dayjs(iso).format('DD.MM.YYYY');
}

export function formatKpiPeriod(period: DashboardKpiPeriod): string {
  const range = `${day(period.from)} — ${day(period.to)}`;
  return period.is_default ? `${range} (период не выбран: последние ${period.days} дней)` : range;
}

export function KpiCards({ kpi, loading, onStuckClick }: KpiCardsProps): ReactNode {
  const pending = loading || !kpi;
  const period = kpi?.period ?? null;
  const range = period ? `${day(period.from)} — ${day(period.to)}` : 'период';
  const previous = period ? `${day(period.previous.from)} — ${day(period.previous.to)}` : '';
  const won = kpi?.completed.value ?? 0;
  const lost = kpi?.lost?.value ?? 0;
  return (
    <div className="flex flex-col gap-2">
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
            hint="Открытые заявки на текущую дату с фильтрами панели"
            loading={pending}
          />
        </motion.div>
        <motion.div variants={staggerItem}>
          <StatCard
            title="Завершено за период"
            icon={CircleCheck}
            tone="success"
            value={won}
            delta={deltaOf(kpi?.completed)}
            hint={`Вход в этап «Завершена» за ${range} (по истории переходов). Дельта — к ${previous}`}
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
            hint={`Завершено / (завершено + отказ) за ${range}: ${won} из ${won + lost}`}
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
            hint="Заявки без движения дольше порога на текущую дату. Открыть на доске"
            onClick={onStuckClick}
            loading={pending}
          />
        </motion.div>
      </motion.div>
      {period ? (
        <p className="text-pretty text-xs text-muted-foreground" data-testid="kpi-period">
          Завершено, отказ ({lost}) и конверсия — за {formatKpiPeriod(period)}; активные и
          зависшие — на текущую дату.
        </p>
      ) : null}
    </div>
  );
}
