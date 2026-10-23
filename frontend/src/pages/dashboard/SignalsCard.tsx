import { ArrowRight, ChevronRight, FileClock, Flame, School, UserRoundX } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import type {
  DashboardSignals,
  LicenseSignal,
  UntouchedSignal,
} from '@/shared/api/endpoints/reports';
import { errorMessage } from '@/shared/api/errors';
import { cn } from '@/shared/lib/cn';
import { dayjs } from '@/shared/lib/dayjs';
import { Button } from '@/shared/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/ui/dialog';
import { Skeleton } from '@/shared/ui/skeleton';
import {
  SIGNAL_THRESHOLDS,
  countTone,
  daysLeftText,
  licenseCaption,
  licenseTone,
  licenseValue,
  signalAriaLabel,
  signalValueText,
  stuckCaption,
  toneLabel,
  transferStatusLabel,
  unassignedCaption,
  untouchedCaption,
  type SignalTone,
} from './signalsModel';

/**
 * Карточка «Сигналы» (ux.md §6.1): что требует внимания руководителя прямо сейчас —
 * зависшие заявки, заявки без ответственного КАМа, лицензии (истекают и истекли), вузы без
 * активности. Срез на текущий момент: период панели не применяется, остальные фильтры —
 * да. Плитка — кнопка: зависшие и «без ответственного» открывают доску с фильтром,
 * лицензии и вузы — окно со списком и переходом в карточки. Ноль — «нет» без тревожного
 * цвета; тон дублируется словом (не только цветом).
 */

interface SignalsCardProps {
  data: DashboardSignals | null;
  loading: boolean;
  error: unknown;
  onRetry: () => void;
  /** Роль видит B2B, но на панели выбран B2C: плитки B2B — с подсказкой, а не пропадают. */
  b2bHidden: boolean;
  onOpenStuck: () => void;
  onOpenUnassigned: () => void;
  /** Ссылка на «Активность КАМов» за период сигнала с фильтрами панели. */
  kamActivityHref: (period: { from: string; to: string }) => string;
}

/** Литеральные классы токенов (как у StatCard): спокойно — нейтрально, без тревожного цвета. */
const TONE_ICON: Record<SignalTone, string> = {
  calm: 'bg-muted text-muted-foreground',
  warning: 'bg-status-warning-tint text-status-warning-deep',
  critical: 'bg-status-danger-tint text-status-danger-deep',
};
const TONE_EDGE: Record<SignalTone, string> = {
  calm: 'border-l-border',
  warning: 'border-l-status-warning',
  critical: 'border-l-status-danger',
};
const TONE_BADGE: Record<SignalTone, string> = {
  calm: '',
  warning: 'bg-status-warning-tint text-status-warning-deep',
  critical: 'bg-status-danger-tint text-status-danger-deep',
};

function day(iso: string): string {
  return dayjs(iso).format('DD.MM.YYYY');
}

interface TileProps {
  icon: LucideIcon;
  title: string;
  value: number;
  caption: string;
  tone: SignalTone;
  action: string;
  onClick: () => void;
}

function SignalTile({ icon: Icon, title, value, caption, tone, action, onClick }: TileProps): ReactNode {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={signalAriaLabel(title, value, tone, caption, action)}
      className={cn(
        'group flex h-full w-full items-start gap-3 rounded-md border border-l-4 border-border bg-card p-3 text-left transition-colors',
        'hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        TONE_EDGE[tone],
      )}
    >
      <span
        className={cn('flex size-9 shrink-0 items-center justify-center rounded-md', TONE_ICON[tone])}
        aria-hidden="true"
      >
        <Icon className="size-5" />
      </span>
      <span className="flex min-w-0 flex-1 flex-col gap-0.5" aria-hidden="true">
        <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
          <span className="text-xs font-medium leading-4 text-muted-foreground">{title}</span>
          {tone !== 'calm' ? (
            <span className={cn('rounded-sm px-1.5 text-[11px] font-medium leading-4', TONE_BADGE[tone])}>
              {toneLabel(tone)}
            </span>
          ) : null}
        </span>
        <span
          className={cn(
            'tabular text-2xl font-semibold leading-8',
            value > 0 ? 'text-foreground' : 'text-muted-foreground',
          )}
        >
          {signalValueText(value)}
        </span>
        <span className="text-pretty text-xs leading-4 text-muted-foreground">{caption}</span>
      </span>
      <ChevronRight
        className="mt-1 size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5"
        aria-hidden="true"
      />
    </button>
  );
}

function B2bOnlyTile({ icon: Icon, title }: { icon: LucideIcon; title: string }): ReactNode {
  return (
    <div className="flex h-full items-start gap-3 rounded-md border border-dashed border-border p-3">
      <span
        className="flex size-9 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground"
        aria-hidden="true"
      >
        <Icon className="size-5" />
      </span>
      <span className="flex min-w-0 flex-col gap-0.5">
        <span className="text-xs font-medium leading-4 text-muted-foreground">{title}</span>
        <span className="text-pretty text-xs leading-4 text-muted-foreground">
          Сигнал процесса B2B — выберите на панели B2B или «Все»
        </span>
      </span>
    </div>
  );
}

function LicensesDialog({
  signal,
  open,
  onOpenChange,
}: {
  signal: LicenseSignal;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}): ReactNode {
  const groups = [
    { state: 'expired' as const, title: `Истекли (${signal.expired})` },
    { state: 'expiring' as const, title: `Истекают в ближайшие ${signal.horizon_days} дней (${signal.expiring})` },
  ];
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Лицензии: истекают и истекли</DialogTitle>
          <DialogDescription>
            Строки действующих договоров с вузами; последний день лицензии — включительно, по МСК.
          </DialogDescription>
        </DialogHeader>
        {licenseValue(signal) === 0 ? (
          <p className="text-sm text-muted-foreground">{licenseCaption(signal)}.</p>
        ) : (
          groups.map((group) => {
            const items = signal.items.filter((item) => item.state === group.state);
            if (items.length === 0) return null;
            return (
              <section key={group.state} aria-label={group.title} className="flex flex-col gap-2">
                <h3 className="text-sm font-semibold">{group.title}</h3>
                <ul className="flex flex-col divide-y divide-border rounded-md border border-border">
                  {items.map((item) => (
                    <li
                      key={`${item.contract_id}-${item.product_id}`}
                      className="flex flex-col gap-0.5 px-3 py-2 text-sm"
                    >
                      <span className="flex flex-wrap items-baseline justify-between gap-x-3">
                        <Link
                          to={`/registry/contracts/${item.contract_id}`}
                          className="font-medium text-primary underline-offset-4 hover:underline"
                        >
                          {item.university} · {item.product}
                        </Link>
                        <span
                          className={cn(
                            'tabular text-xs font-medium',
                            item.state === 'expired'
                              ? 'text-status-danger-deep'
                              : 'text-status-warning-deep',
                          )}
                        >
                          до {day(item.valid_to)}, {daysLeftText(item.days_left)}
                        </span>
                      </span>
                      <span className="text-xs text-muted-foreground">
                        Договор {item.contract_number} · {transferStatusLabel(item.transfer_status)}
                        {item.kam ? ` · КАМ: ${item.kam}` : ''}
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            );
          })
        )}
        {signal.truncated ? (
          <p className="text-xs text-muted-foreground">
            Показаны первые 50 строк каждой группы — уточните фильтры панели.
          </p>
        ) : null}
        <DialogFooter>
          <Button variant="outline" asChild>
            <Link to="/registry/contracts">
              Реестр договоров
              <ArrowRight aria-hidden="true" />
            </Link>
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function UntouchedDialog({
  signal,
  href,
  open,
  onOpenChange,
}: {
  signal: UntouchedSignal;
  href: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}): ReactNode {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Вузы без активности</DialogTitle>
          <DialogDescription>
            Закреплены за КАМом, но за {day(signal.period.from)} — {day(signal.period.to)} не было ни
            одного взаимодействия: перехода, комментария, файла или мероприятия.
          </DialogDescription>
        </DialogHeader>
        {signal.value === 0 ? (
          <p className="text-sm text-muted-foreground">{untouchedCaption(signal)}.</p>
        ) : (
          <ul className="flex flex-col divide-y divide-border rounded-md border border-border">
            {signal.items.map((item) => (
              <li key={item.university_id} className="flex flex-col gap-0.5 px-3 py-2 text-sm">
                <Link
                  to={`/registry/universities/${item.university_id}`}
                  className="font-medium text-primary underline-offset-4 hover:underline"
                >
                  {item.name}
                </Link>
                <span className="text-xs text-muted-foreground">
                  КАМ: {item.kam} · последний контакт:{' '}
                  {item.last_contact_at ? day(item.last_contact_at) : 'не было'}
                </span>
              </li>
            ))}
          </ul>
        )}
        {signal.truncated ? (
          <p className="text-xs text-muted-foreground">
            Показаны первые 50 вузов из {signal.value} — полный список в отчёте.
          </p>
        ) : null}
        <DialogFooter>
          <Button variant="outline" asChild>
            <Link to={href}>
              Открыть «Активность КАМов»
              <ArrowRight aria-hidden="true" />
            </Link>
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function SignalsCard({
  data,
  loading,
  error,
  onRetry,
  b2bHidden,
  onOpenStuck,
  onOpenUnassigned,
  kamActivityHref,
}: SignalsCardProps): ReactNode {
  const [dialog, setDialog] = useState<'licenses' | 'untouched' | null>(null);
  const close = (open: boolean): void => {
    if (!open) setDialog(null);
  };

  let body: ReactNode;
  if (error && !data) {
    body = (
      <div role="alert" className="flex flex-wrap items-center gap-3 px-5 pb-5 pt-2 text-sm">
        <span className="text-muted-foreground">Не удалось загрузить сигналы: {errorMessage(error)}</span>
        <Button variant="outline" size="sm" onClick={onRetry}>
          Повторить
        </Button>
      </div>
    );
  } else if (loading || !data) {
    body = (
      <div className="grid grid-cols-1 gap-3 px-5 pb-5 pt-2 sm:grid-cols-2 xl:grid-cols-4" aria-busy="true">
        {[0, 1, 2, 3].map((index) => (
          <Skeleton key={index} className="h-[104px] rounded-md" />
        ))}
      </div>
    );
  } else {
    const licenses = data.licenses;
    const untouched = data.untouched_universities;
    body = (
      <ul className="grid grid-cols-1 gap-3 px-5 pb-5 pt-2 sm:grid-cols-2 xl:grid-cols-4">
        <li>
          <SignalTile
            icon={Flame}
            title="Зависшие заявки"
            value={data.stuck.value}
            caption={stuckCaption(data.stuck)}
            tone={countTone(data.stuck.value, SIGNAL_THRESHOLDS.stuck)}
            action="Открыть доску с фильтром «Только зависшие»"
            onClick={onOpenStuck}
          />
        </li>
        <li>
          <SignalTile
            icon={UserRoundX}
            title="Без ответственного КАМа"
            value={data.unassigned.value}
            caption={unassignedCaption(data.unassigned)}
            tone={countTone(data.unassigned.value, SIGNAL_THRESHOLDS.unassigned)}
            action="Открыть доску с фильтром «Без ответственного КАМа»"
            onClick={onOpenUnassigned}
          />
        </li>
        {licenses ? (
          <li>
            <SignalTile
              icon={FileClock}
              title="Лицензии"
              value={licenseValue(licenses)}
              caption={licenseCaption(licenses)}
              tone={licenseTone(licenses)}
              action="Открыть список лицензий"
              onClick={() => setDialog('licenses')}
            />
          </li>
        ) : b2bHidden ? (
          <li>
            <B2bOnlyTile icon={FileClock} title="Лицензии" />
          </li>
        ) : null}
        {untouched ? (
          <li>
            <SignalTile
              icon={School}
              title="Вузы без активности"
              value={untouched.value}
              caption={untouchedCaption(untouched)}
              tone={countTone(untouched.value, SIGNAL_THRESHOLDS.untouched)}
              action="Открыть список вузов"
              onClick={() => setDialog('untouched')}
            />
          </li>
        ) : b2bHidden ? (
          <li>
            <B2bOnlyTile icon={School} title="Вузы без активности" />
          </li>
        ) : null}
      </ul>
    );
  }

  return (
    <section
      aria-labelledby="dashboard-signals-title"
      className="flex flex-col rounded-lg border border-border bg-card shadow-card"
    >
      <header className="px-5 pb-1 pt-4">
        <h2 id="dashboard-signals-title" className="text-base font-semibold">
          Сигналы
        </h2>
        <p className="mt-0.5 text-pretty text-xs text-muted-foreground">
          Что требует внимания на текущий момент: период панели не применяется, процесс, КАМ, вуз
          и другие фильтры — применяются
          {data ? ` · данные на ${dayjs(data.as_of).format('DD.MM.YYYY HH:mm')}` : ''}
        </p>
      </header>
      {body}
      {data?.licenses ? (
        <LicensesDialog
          signal={data.licenses}
          open={dialog === 'licenses'}
          onOpenChange={close}
        />
      ) : null}
      {data?.untouched_universities ? (
        <UntouchedDialog
          signal={data.untouched_universities}
          href={kamActivityHref(data.untouched_universities.period)}
          open={dialog === 'untouched'}
          onOpenChange={close}
        />
      ) : null}
    </section>
  );
}
