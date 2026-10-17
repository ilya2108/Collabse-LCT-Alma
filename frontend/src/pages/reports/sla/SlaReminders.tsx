import { BellRing, ChevronDown } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import type {
  SlaComparison,
  SlaEpisodeStatus,
  SlaKamReminders,
  SlaKamTeamRef,
  SlaReminderEpisode,
} from '@/shared/api/endpoints/sla';
import type { SemanticStatus } from '@/shared/config/tokens';
import { Button } from '@/shared/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/shared/ui/collapsible';
import { StageTag, StatusBadge } from '@/shared/ui/data-table';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table';
import { RequestLink } from '../KamActivityParts';
import {
  EMPTY,
  actionKindLabel,
  deltaCaption,
  formatHours,
  formatPct,
  mskDateTime,
  mskDay,
  ofText,
} from './slaFormat';

/**
 * Напоминания в разборе КАМа: факты журнала уведомлений по событию «заявка зависла» —
 * доставлено n из m, визитов с напоминанием, эскалировано; первое действие в системе
 * после напоминания (в течение 72 ч — доля «6 из 8», медиана часов, ожидают). Пустой
 * журнал — одна строка. Дельта к прошлому периоду — только при полном покрытии журналом.
 */

const STATUS_META: Record<SlaEpisodeStatus, { label: string; status: SemanticStatus }> = {
  reacted_72h: { label: 'действие в течение 72 ч', status: 'success' },
  no_action: { label: 'без действий в системе', status: 'danger' },
  pending: { label: 'ожидает: 72 ч не прошли', status: 'draft' },
  undelivered: { label: 'не доставлено', status: 'draft' },
};

interface SlaRemindersProps {
  reminders: SlaKamReminders;
  comparison: SlaComparison;
  team: SlaKamTeamRef | null;
  compact: boolean;
  onOpenRequest: (id: string) => void;
}

function n(value: number | null | undefined): number {
  return typeof value === 'number' ? value : 0;
}

export function SlaReminders({
  reminders,
  comparison,
  team,
  compact,
  onOpenRequest,
}: SlaRemindersProps): ReactNode {
  const [open, setOpen] = useState(false);
  const info = reminders.info;
  const disabled = !info.check_enabled ? (
    <p className="text-sm text-status-warning-deep">
      Проверка зависших выключена
      {info.disabled_since ? ` с ${mskDay(info.disabled_since)}` : ''}: напоминания не отправляются.
    </p>
  ) : null;

  if (info.coverage === 'none') {
    return (
      <Shell>
        <p className="text-sm text-muted-foreground">Журнал напоминаний пуст.</p>
        {disabled}
      </Shell>
    );
  }

  const sent = n(reminders.sent);
  const delivered = n(reminders.delivered);
  const matured = n(reminders.matured);
  const reacted = n(reminders.reacted_72h);
  const pending = n(reminders.pending);
  const undelivered = n(reminders.undelivered);
  const noAction = n(reminders.no_action);
  const items = reminders.items ?? [];
  const showDelta = info.prev_coverage === 'full' && reminders.prev;
  const prevPct = reminders.prev?.reacted_72h_pct ?? null;
  const pctDelta =
    showDelta && prevPct !== null && reminders.reacted_72h_pct !== null && reminders.reacted_72h_pct !== undefined
      ? reminders.reacted_72h_pct - prevPct
      : null;
  const pctDeltaText = deltaCaption(pctDelta, 'pct', comparison.period_label);

  return (
    <Shell>
      <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
        <div>
          <dt className="text-xs text-muted-foreground">Напоминания за период</dt>
          <dd className="tabular">
            Доставлено {ofText(delivered, sent)} · визитов с напоминанием {n(reminders.visits_reminded)} ·
            эскалировано {n(reminders.visits_escalated)}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Первое действие в системе после напоминания</dt>
          <dd className="tabular">
            {matured > 0 ? (
              <>
                в течение 72 ч — {ofText(reacted, matured)} ({formatPct(reminders.reacted_72h_pct)})
              </>
            ) : (
              'эпизодов, по которым прошло 72 ч, нет'
            )}
            {reminders.reaction_median_hours !== null && reminders.reaction_median_hours !== undefined
              ? `; медиана ${formatHours(reminders.reaction_median_hours)}`
              : ''}
            {`; ожидают — ${pending}`}
            {undelivered > 0 ? `; не доставлено — ${undelivered}` : ''}
          </dd>
          {pctDeltaText ? <dd className="text-xs text-muted-foreground">{pctDeltaText}</dd> : null}
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Без действий в системе после напоминания</dt>
          <dd className="tabular">{noAction}</dd>
        </div>
        {team ? (
          <div>
            <dt className="text-xs text-muted-foreground">Справочно по команде</dt>
            <dd className="tabular">
              в течение 72 ч — {formatPct(team.reacted_72h_pct)}; медиана {formatHours(team.reaction_median_hours)}
            </dd>
          </div>
        ) : null}
      </dl>
      {disabled}
      {info.coverage === 'partial' && info.journal_from ? (
        <p className="text-xs text-muted-foreground">
          Журнал напоминаний ведётся с {mskDay(info.journal_from)}: период покрыт не полностью.
        </p>
      ) : null}
      {info.prev_coverage !== 'full' ? (
        <p className="text-xs text-muted-foreground">
          Сравнение с прошлым периодом не показано: журнал покрывает его не полностью.
        </p>
      ) : null}
      {!info.rules_active ? (
        <p className="text-xs text-muted-foreground">
          Активного правила напоминаний ответственному о зависших заявках нет.
        </p>
      ) : null}
      {info.note ? <p className="text-pretty text-xs text-muted-foreground">{info.note}</p> : null}

      {items.length > 0 ? (
        <Collapsible open={open} onOpenChange={setOpen}>
          <CollapsibleTrigger asChild>
            <Button variant="ghost" size="sm" className="h-8 self-start px-2 text-muted-foreground">
              Эпизоды напоминаний ({items.length})
              <ChevronDown
                className={open ? 'rotate-180 transition-transform' : 'transition-transform'}
                aria-hidden="true"
              />
            </Button>
          </CollapsibleTrigger>
          <CollapsibleContent className="pt-1">
            {compact ? (
              <EpisodeCards items={items} onOpenRequest={onOpenRequest} />
            ) : (
              <EpisodeTable items={items} onOpenRequest={onOpenRequest} />
            )}
          </CollapsibleContent>
        </Collapsible>
      ) : null}
    </Shell>
  );
}

function Shell({ children }: { children: ReactNode }): ReactNode {
  return (
    <section className="flex min-w-0 flex-col gap-2 rounded-lg border border-border bg-card p-4 shadow-card sm:p-5">
      <h2 className="flex items-center gap-2 text-base font-semibold">
        <BellRing className="size-4 text-muted-foreground" aria-hidden="true" />
        Напоминания
      </h2>
      {children}
    </section>
  );
}

function reactionText(item: SlaReminderEpisode): string {
  if (!item.reaction_at) return EMPTY;
  const hours = item.reaction_hours !== null ? ` через ${formatHours(item.reaction_hours)}` : '';
  return `${actionKindLabel(item.reaction_kind)}${hours}`;
}

function EpisodeTable({
  items,
  onOpenRequest,
}: {
  items: SlaReminderEpisode[];
  onOpenRequest: (id: string) => void;
}): ReactNode {
  return (
    <div className="max-w-full overflow-x-auto rounded-lg border border-border">
      <Table className="min-w-[760px]">
        <TableHeader>
          <TableRow>
            <TableHead>Заявка</TableHead>
            <TableHead>Этап</TableHead>
            <TableHead>Первое напоминание</TableHead>
            <TableHead>Доставлено</TableHead>
            <TableHead>Статус</TableHead>
            <TableHead>Первое действие</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((item) => (
            <TableRow key={`${item.request_id}:${item.first_reminder_at}`}>
              <TableCell>
                <RequestLink request={item} onOpen={onOpenRequest} className="text-sm" />
              </TableCell>
              <TableCell>
                <StageTag size="sm" color={item.stage.color} name={item.stage.name} />
              </TableCell>
              <TableCell className="tabular">{mskDateTime(item.first_reminder_at)}</TableCell>
              <TableCell className="tabular">{mskDateTime(item.delivered_at)}</TableCell>
              <TableCell>
                <StatusBadge status={STATUS_META[item.status].status} size="sm">
                  {STATUS_META[item.status].label}
                </StatusBadge>
              </TableCell>
              <TableCell className="tabular">{reactionText(item)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function EpisodeCards({
  items,
  onOpenRequest,
}: {
  items: SlaReminderEpisode[];
  onOpenRequest: (id: string) => void;
}): ReactNode {
  return (
    <ul className="flex flex-col gap-2">
      {items.map((item) => (
        <li
          key={`${item.request_id}:${item.first_reminder_at}`}
          className="flex min-w-0 flex-col gap-1 rounded-md border border-border p-3"
        >
          <RequestLink request={item} onOpen={onOpenRequest} className="text-sm" />
          <span className="flex flex-wrap items-center gap-1.5">
            <StageTag size="sm" color={item.stage.color} name={item.stage.name} />
            <StatusBadge status={STATUS_META[item.status].status} size="sm">
              {STATUS_META[item.status].label}
            </StatusBadge>
          </span>
          <span className="tabular text-xs text-muted-foreground">
            Напоминание {mskDateTime(item.first_reminder_at)}
            {item.reaction_at ? ` · ${reactionText(item)}` : ''}
          </span>
        </li>
      ))}
    </ul>
  );
}
