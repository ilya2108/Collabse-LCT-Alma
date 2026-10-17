import { CircleCheck, Clock3, Flame, Hourglass, TrendingDown, TrendingUp } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { motion } from 'motion/react';
import type { ReactNode } from 'react';
import type {
  SlaComparison,
  SlaKamTeamRef,
  SlaKpi,
  SlaKpiKey,
  SlaKpiValue,
} from '@/shared/api/endpoints/sla';
import { cn } from '@/shared/lib/cn';
import { staggerContainer, staggerItem } from '@/shared/lib/motion';
import { Skeleton } from '@/shared/ui/skeleton';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/ui/tooltip';
import {
  EMPTY,
  deltaCaption,
  deltaTone,
  formatDays,
  formatDelay,
  formatInt,
  formatPct,
  ofText,
  plural,
  samplePct,
  type DeltaMode,
  type DeltaUnit,
} from './slaFormat';

/**
 * 4 KPI вкладки «Просрочки»: «Просрочено», «После срока без действий», «Больше 30 дн.
 * сверх нормы», «Уложились в норму». Дельта подписана, к чему она: остатки — к срезу на
 * начало периода («к 01.10»), показатели за период — к прошлому периоду («к 15.09–30.09»).
 * У первых трёх рост — это плохо (красным). В разборе КАМа — справочно «по команде».
 */

type Tone = 'warning' | 'danger' | 'success' | 'draft';

const TONE_CLASSES: Record<Tone, string> = {
  warning: 'bg-status-warning-tint text-status-warning-deep',
  danger: 'bg-status-danger-tint text-status-danger-deep',
  success: 'bg-status-success-tint text-status-success-deep',
  draft: 'bg-status-draft-tint text-status-draft-deep',
};

interface KpiCardProps {
  title: string;
  icon: LucideIcon;
  tone: Tone;
  value: string;
  caption?: ReactNode;
  detail?: ReactNode;
  reference?: string | null;
  delta?: string | null;
  deltaTone?: 'good' | 'bad' | 'neutral';
  deltaUp?: boolean;
  hint: string;
  onClick?: () => void;
}

function KpiCard({
  title,
  icon: Icon,
  tone,
  value,
  caption,
  detail,
  reference,
  delta,
  deltaTone: toneOfDelta = 'neutral',
  deltaUp,
  hint,
  onClick,
}: KpiCardProps): ReactNode {
  const DeltaIcon = deltaUp ? TrendingUp : TrendingDown;
  const body = (
    <>
      <div className="flex items-start gap-2.5">
        <span
          className={cn(
            'flex size-9 shrink-0 items-center justify-center rounded-md sm:size-10',
            TONE_CLASSES[tone],
          )}
        >
          <Icon className="size-5" aria-hidden="true" />
        </span>
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="line-clamp-2 text-xs font-medium leading-4 text-muted-foreground">{title}</span>
          <span className="tabular text-2xl font-semibold leading-8 text-foreground sm:text-[28px] sm:leading-9">
            {value}
          </span>
        </span>
      </div>
      <div className="flex min-w-0 flex-col gap-0.5 text-xs">
        {caption ? <span className="tabular text-muted-foreground">{caption}</span> : null}
        {detail ? <span className="tabular text-muted-foreground">{detail}</span> : null}
        {delta ? (
          <span
            className={cn(
              'tabular inline-flex items-center gap-1 font-medium',
              toneOfDelta === 'good' && 'text-status-success-deep',
              toneOfDelta === 'bad' && 'text-status-danger-deep',
              toneOfDelta === 'neutral' && 'text-muted-foreground',
            )}
          >
            {deltaUp !== undefined ? <DeltaIcon className="size-3" aria-hidden="true" /> : null}
            {delta}
          </span>
        ) : null}
        {reference ? <span className="tabular text-muted-foreground">{reference}</span> : null}
      </div>
    </>
  );
  const className =
    'flex h-full w-full flex-col gap-2 rounded-lg border border-border bg-card p-4 text-left shadow-card sm:p-5';
  const card = onClick ? (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        className,
        'cursor-pointer transition-[box-shadow,transform] duration-150 hover:-translate-y-0.5 hover:shadow-overlay focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
      )}
    >
      {body}
    </button>
  ) : (
    <div className={className}>{body}</div>
  );
  return (
    <Tooltip>
      <TooltipTrigger asChild>{card}</TooltipTrigger>
      <TooltipContent className="max-w-xs">{hint}</TooltipContent>
    </Tooltip>
  );
}

function KpiSkeleton(): ReactNode {
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4 shadow-card sm:p-5">
      <div className="flex items-center gap-2.5">
        <Skeleton className="size-9 shrink-0 rounded-md sm:size-10" />
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <Skeleton className="h-3 w-2/3 rounded-sm" />
          <Skeleton className="h-7 w-1/3 rounded-sm" />
        </div>
      </div>
      <Skeleton className="h-3 w-3/4 rounded-sm" />
    </div>
  );
}

function num(kpi: SlaKpi, key: SlaKpiKey): number | null {
  return kpi[key]?.value ?? null;
}

function deltaProps(
  item: SlaKpiValue | undefined,
  unit: DeltaUnit,
  mode: DeltaMode,
  comparison: SlaComparison,
): Pick<KpiCardProps, 'delta' | 'deltaTone' | 'deltaUp'> {
  if (!item) return {};
  const label = item.compare === 'period' ? comparison.period_label : comparison.snapshot_label;
  const text = deltaCaption(item.delta, unit, label);
  if (!text) return {};
  const tone = deltaTone(item.delta, mode);
  const delta = item.delta ?? 0;
  return {
    delta: text,
    deltaTone: tone,
    deltaUp: Math.round(delta * 10) === 0 ? undefined : delta > 0,
  };
}

interface SlaKpiRowProps {
  kpi: SlaKpi | null;
  comparison: SlaComparison | null;
  loading?: boolean;
  /** Разбор КАМа: «в среднем» первым, справочно — по команде. */
  variant?: 'team' | 'kam';
  team?: SlaKamTeamRef | null;
  /** Клик по «Просрочено» — доска с фильтром «Зависшие» (только для текущего периода). */
  onOverdueClick?: () => void;
}

export function SlaKpiRow({
  kpi,
  comparison,
  loading = false,
  variant = 'team',
  team = null,
  onOverdueClick,
}: SlaKpiRowProps): ReactNode {
  if (loading || !kpi || !comparison) {
    return (
      <div className="grid grid-cols-1 gap-3 min-[440px]:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }, (_, index) => (
          <KpiSkeleton key={index} />
        ))}
      </div>
    );
  }

  const active = num(kpi, 'active') ?? 0;
  const overdue = num(kpi, 'overdue') ?? 0;
  const inherited = num(kpi, 'inherited') ?? 0;
  const pct = samplePct(num(kpi, 'overdue_pct'), active);
  const median = num(kpi, 'delay_median_days');
  const avg = num(kpi, 'delay_avg_days');
  const max = num(kpi, 'delay_max_days');
  const decided = num(kpi, 'decided') ?? 0;
  const onTime = num(kpi, 'on_time') ?? 0;
  const onTimePct = num(kpi, 'on_time_pct');

  const overdueDetail =
    overdue === 0
      ? null
      : variant === 'kam'
        ? `в среднем ${formatDelay(avg)} по ${overdue} ${plural(overdue, [
            'заявке',
            'заявкам',
            'заявкам',
          ])} · медиана ${formatDelay(median)} · максимум ${formatDays(max)}`
        : `обычно ${formatDelay(median)}, максимум ${formatDays(max)}`;

  const overdueCaption = (
    <>
      из {formatInt(active)} в работе ·{' '}
      <span className={cn(pct.small && 'opacity-70')} title={pct.hint ?? undefined}>
        {pct.text}
        {pct.small ? ' (мало заявок)' : ''}
      </span>
      {inherited > 0 ? ` · получено просроченными: ${formatInt(inherited)}` : ''}
    </>
  );

  const showTeam = variant === 'kam' && team !== null;

  return (
    <motion.div
      variants={staggerContainer}
      initial="hidden"
      animate="visible"
      className="grid grid-cols-1 gap-3 min-[440px]:grid-cols-2 xl:grid-cols-4"
    >
      <motion.div variants={staggerItem}>
        <KpiCard
          title="Просрочено"
          icon={Flame}
          tone="warning"
          value={formatInt(overdue)}
          caption={overdueCaption}
          detail={overdueDetail}
          reference={
            showTeam
              ? `по команде: ${formatPct(team?.overdue_pct)}, обычно ${formatDelay(team?.delay_median_days)}`
              : null
          }
          hint="Заявки на этапе дольше нормы этапа — то же, что «зависшие» на других вкладках. Дельта — к срезу на начало периода."
          onClick={onOverdueClick}
          {...deltaProps(kpi.overdue, 'count', 'inverted', comparison)}
        />
      </motion.div>
      <motion.div variants={staggerItem}>
        <KpiCard
          title="После срока без действий"
          icon={Hourglass}
          tone="danger"
          value={formatInt(num(kpi, 'overdue_no_action') ?? 0)}
          caption={`из ${formatInt(overdue)} ${plural(overdue, [
            'просроченной',
            'просроченных',
            'просроченных',
          ])}`}
          hint="После истечения нормы в заявке не было ручного перехода, комментария или файла — любого автора."
          {...deltaProps(kpi.overdue_no_action, 'count', 'inverted', comparison)}
        />
      </motion.div>
      <motion.div variants={staggerItem}>
        <KpiCard
          title="Больше 30 дн. сверх нормы"
          icon={Clock3}
          tone="danger"
          value={formatInt(num(kpi, 'overdue_30_plus') ?? 0)}
          caption={`из ${formatInt(overdue)} ${plural(overdue, [
            'просроченной',
            'просроченных',
            'просроченных',
          ])}`}
          hint="Задержка сверх нормы этапа — больше 30 календарных дней."
          {...deltaProps(kpi.overdue_30_plus, 'count', 'inverted', comparison)}
        />
      </motion.div>
      <motion.div variants={staggerItem}>
        <KpiCard
          title="Уложились в норму"
          icon={CircleCheck}
          tone="success"
          value={decided > 0 && onTimePct !== null ? formatPct(onTimePct) : EMPTY}
          caption={
            decided > 0
              ? `${ofText(onTime, decided)} ${plural(decided, ['этапа', 'этапов', 'этапов'])}`
              : 'решённых этапов за период нет'
          }
          reference={showTeam ? `по команде: ${formatPct(team?.on_time_pct)}` : null}
          hint="Из этапов, по которым в периоде наступило решение: ушли с этапа до срока или срок истёк. Дельта — к прошлому периоду той же длины, в п.п."
          {...deltaProps(kpi.on_time_pct, 'pct', 'normal', comparison)}
        />
      </motion.div>
    </motion.div>
  );
}
