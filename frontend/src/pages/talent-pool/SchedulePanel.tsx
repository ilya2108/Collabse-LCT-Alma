import { useQuery } from '@tanstack/react-query';
import { CalendarDays, MapPin, Users } from 'lucide-react';
import { motion } from 'motion/react';
import { useMemo, useState, type ReactNode } from 'react';
import type { DateRange } from 'react-day-picker';
import { listActivities } from '@/shared/api/endpoints/activities';
import type { Activity, ActivityScheduleEntry } from '@/shared/api/types';
import { dayjs, type Dayjs } from '@/shared/lib/dayjs';
import { staggerContainer, staggerItem } from '@/shared/lib/motion';
import { Button } from '@/shared/ui/button';
import { Calendar } from '@/shared/ui/calendar';
import { Popover, PopoverContent, PopoverTrigger } from '@/shared/ui/popover';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/ui/select';
import { EmptyState, ErrorState, LoadingBlock, StatusBadge } from './local';
import { ACTIVITY_FORMAT_LABELS, ACTIVITY_TYPE_LABELS } from './model';

/**
 * Сквозное расписание активностей карточками (redesign.md §6.7): группы
 * по датам, каждый слот — строка-карточка с временем, типом-бейджем и
 * фактами с иконками. Фильтры: период (Popover+Calendar range с пресетами)
 * и тип активности.
 */

interface ScheduleRow {
  activity: Activity;
  entry: ActivityScheduleEntry | null;
}

function groupByDate(activities: Activity[], from: Dayjs, to: Dayjs): Map<string, ScheduleRow[]> {
  const groups = new Map<string, ScheduleRow[]>();
  const push = (dateKey: string, row: ScheduleRow): void => {
    const bucket = groups.get(dateKey);
    if (bucket) bucket.push(row);
    else groups.set(dateKey, [row]);
  };
  for (const activity of activities) {
    const slots = activity.schedule ?? [];
    if (slots.length === 0) {
      push('no-date', { activity, entry: null });
      continue;
    }
    for (const entry of slots) {
      const start = dayjs(entry.starts_at);
      if (!start.isValid() || start.isBefore(from, 'day') || start.isAfter(to, 'day')) continue;
      push(start.format('YYYY-MM-DD'), { activity, entry });
    }
  }
  for (const rows of groups.values()) {
    rows.sort((a, b) => (a.entry?.starts_at ?? '').localeCompare(b.entry?.starts_at ?? ''));
  }
  return groups;
}

const RANGE_PRESETS: Array<{ label: string; days: number }> = [
  { label: 'Месяц', days: 30 },
  { label: 'Квартал', days: 90 },
  { label: 'Год', days: 365 },
];

function ScheduleRowCard({ activity, entry }: ScheduleRow): ReactNode {
  return (
    <motion.li
      variants={staggerItem}
      className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border px-4 py-2.5 text-sm first:border-t-0"
    >
      <span className="w-24 shrink-0 text-muted-foreground tabular">
        {entry ? dayjs(entry.starts_at).format('HH:mm') : '—'}
        {entry?.ends_at ? `–${dayjs(entry.ends_at).format('HH:mm')}` : ''}
      </span>
      <span className="min-w-0 flex-1 truncate font-medium">{activity.name}</span>
      <StatusBadge status="talent" size="sm">
        {ACTIVITY_TYPE_LABELS[activity.activity_type] ?? activity.activity_type}
      </StatusBadge>
      {entry?.format ? (
        <StatusBadge status="draft" size="sm">
          {ACTIVITY_FORMAT_LABELS[entry.format] ?? entry.format}
        </StatusBadge>
      ) : null}
      {entry?.location ? (
        <span className="flex items-center gap-1 text-xs text-muted-foreground">
          <MapPin className="size-3" aria-hidden="true" />
          {entry.location}
        </span>
      ) : null}
      {activity.federal_project?.name ? (
        <StatusBadge status="progress" size="sm">
          {activity.federal_project.name}
        </StatusBadge>
      ) : null}
      {typeof activity.participants_count === 'number' ? (
        <span className="flex items-center gap-1 text-xs text-muted-foreground tabular">
          <Users className="size-3" aria-hidden="true" />
          {activity.participants_count}
        </span>
      ) : null}
    </motion.li>
  );
}

export function SchedulePanel(): ReactNode {
  const [range, setRange] = useState<[Dayjs, Dayjs]>(() => [dayjs(), dayjs().add(60, 'day')]);
  const [kind, setKind] = useState<string | undefined>(undefined);
  const [calendarOpen, setCalendarOpen] = useState(false);

  const query = useQuery({
    queryKey: [
      'activities',
      range[0].format('YYYY-MM-DD'),
      range[1].format('YYYY-MM-DD'),
      kind ?? 'all',
    ],
    queryFn: ({ signal }) =>
      listActivities(
        {
          from: range[0].format('YYYY-MM-DD'),
          to: range[1].format('YYYY-MM-DD'),
          kind,
        },
        signal,
      ),
    staleTime: 60_000,
  });

  const groups = useMemo(() => {
    const items = query.data?.items ?? [];
    return groupByDate(items, range[0], range[1]);
  }, [query.data, range]);

  const dateKeys = [...groups.keys()].filter((k) => k !== 'no-date').sort();
  const noDateRows = groups.get('no-date') ?? [];

  const selectedRange: DateRange = { from: range[0].toDate(), to: range[1].toDate() };

  let body: ReactNode;
  if (query.isLoading) {
    body = <LoadingBlock rows={6} />;
  } else if (query.isError) {
    body = (
      <ErrorState
        error={query.error}
        title="Не удалось загрузить расписание"
        onRetry={() => void query.refetch()}
      />
    );
  } else if (dateKeys.length === 0 && noDateRows.length === 0) {
    body = (
      <EmptyState
        illustration="search-empty"
        title="Активностей за период нет"
        description="Измените период или тип активности"
      />
    );
  } else {
    body = (
      <motion.div
        variants={staggerContainer}
        initial="hidden"
        animate="visible"
        className="space-y-4"
      >
        {dateKeys.map((dateKey) => (
          <motion.section key={dateKey} variants={staggerItem}>
            <h3 className="mb-2 text-base font-semibold capitalize">
              {dayjs(dateKey).format('dddd, D MMM YYYY')}
            </h3>
            <ul className="rounded-lg border bg-card shadow-card">
              {(groups.get(dateKey) ?? []).map((row) => (
                <ScheduleRowCard
                  key={`${row.activity.id}-${row.entry?.id ?? 'none'}`}
                  {...row}
                />
              ))}
            </ul>
          </motion.section>
        ))}
        {noDateRows.length > 0 ? (
          <motion.section variants={staggerItem}>
            <h3 className="mb-2 text-base font-semibold">Без назначенной даты</h3>
            <ul className="rounded-lg border bg-card shadow-card">
              {noDateRows.map((row) => (
                <ScheduleRowCard key={row.activity.id} {...row} />
              ))}
            </ul>
          </motion.section>
        ) : null}
      </motion.div>
    );
  }

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Popover open={calendarOpen} onOpenChange={setCalendarOpen}>
          <PopoverTrigger asChild>
            <Button variant="outline" className="font-normal">
              <CalendarDays className="size-4 text-muted-foreground" aria-hidden="true" />
              {range[0].format('D MMM YYYY')} – {range[1].format('D MMM YYYY')}
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-auto p-0" align="start">
            <Calendar
              mode="range"
              numberOfMonths={2}
              selected={selectedRange}
              onSelect={(next) => {
                if (next?.from && next.to) {
                  setRange([dayjs(next.from), dayjs(next.to)]);
                }
              }}
            />
            <div className="flex gap-2 border-t p-2">
              {RANGE_PRESETS.map((preset) => (
                <Button
                  key={preset.label}
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setRange([dayjs(), dayjs().add(preset.days, 'day')]);
                    setCalendarOpen(false);
                  }}
                >
                  {preset.label}
                </Button>
              ))}
            </div>
          </PopoverContent>
        </Popover>
        <Select
          value={kind ?? 'all'}
          onValueChange={(value) => setKind(value === 'all' ? undefined : value)}
        >
          <SelectTrigger className="w-48" aria-label="Тип активности">
            <SelectValue placeholder="Тип активности" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Все типы</SelectItem>
            {Object.entries(ACTIVITY_TYPE_LABELS).map(([value, label]) => (
              <SelectItem key={value} value={value}>
                {label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {body}
    </div>
  );
}
