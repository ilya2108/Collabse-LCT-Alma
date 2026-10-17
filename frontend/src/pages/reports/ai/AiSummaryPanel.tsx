import {
  Copy,
  EyeOff,
  Info,
  ListChecks,
  LoaderCircle,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
  X,
} from 'lucide-react';
import type { ReactNode } from 'react';
import type { AiSummaryJob, AiSummaryPerson } from '@/shared/api/endpoints/aiSummary';
import { errorMessage } from '@/shared/api/errors';
import { cn } from '@/shared/lib/cn';
import { toastError, toastSuccess } from '@/shared/lib/toast';
import { Button } from '@/shared/ui/button';
import { Skeleton } from '@/shared/ui/skeleton';
import {
  checksCaption,
  errorView,
  isLongQueue,
  isPending,
  mskDateTime,
  periodLabel,
  progressText,
  sectionList,
  sourceCaption,
  summaryTitle,
  toPlainText,
  type AiSectionKey,
  type AiSectionView,
} from './aiSummaryModel';
import type { AiSummaryController } from './useAiSummary';

/**
 * Панель «Сводка ИИ» (ux.md §6.4): состояния «собираем данные» → «в очереди / формируется»
 * → готово или сбой. Готовая сводка — только текст (без HTML и markdown) с плашкой
 * «Сформировано GigaChat — сверяйте цифры с отчётом» и итогом сверки чисел; сбой
 * предлагает сводку по правилам. Ушли фильтры — баннер «за прежние параметры».
 * От 375 px: карточка во всю ширину, действия переносятся.
 */

interface AiSummaryPanelProps {
  controller: AiSummaryController;
  /** Чипы упомянутых КАМов (сводка команды) → разбор КАМа во вкладке «Просрочки». */
  onSelectKam?: (kamId: string) => void;
  className?: string;
}

const SECTION_DOT: Record<Exclude<AiSectionKey, 'headline'>, string> = {
  going_well: 'bg-status-success',
  lagging: 'bg-status-warning',
  attention: 'bg-status-danger',
  questions: 'bg-status-progress',
};

/** Копирование с фолбэком через скрытую textarea (старые браузеры, http без secure context). */
async function copyText(text: string): Promise<void> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
  } catch {
    // ниже — фолбэк
  }
  const area = document.createElement('textarea');
  area.value = text;
  area.setAttribute('readonly', '');
  area.style.position = 'fixed';
  area.style.top = '0';
  area.style.left = '0';
  area.style.opacity = '0';
  area.style.pointerEvents = 'none';
  document.body.appendChild(area);
  area.select();
  const ok = document.execCommand('copy');
  document.body.removeChild(area);
  if (!ok) throw new Error('Буфер обмена недоступен в этом браузере');
}

function liveText(controller: AiSummaryController): string {
  const { job, requestError, pollExpired, busy } = controller;
  if (requestError != null) return 'Не удалось запросить сводку ИИ';
  if (pollExpired) return 'Сводка ИИ формируется дольше обычного';
  if (busy) return 'Сводка ИИ формируется';
  if (job?.status === 'done') return 'Сводка ИИ готова';
  if (job?.status === 'failed') return `Сводка ИИ не сформирована: ${errorView(job.error).title}`;
  return '';
}

export function AiSummaryPanel({ controller, onSelectKam, className }: AiSummaryPanelProps): ReactNode {
  if (!controller.open) return null;
  const { job, busy, requestError, pollExpired, stale } = controller;
  const title = job ? summaryTitle(job) : controller.placeholderTitle;
  const retryMode = job?.mode ?? 'auto';

  let meta: string | null = null;
  if (job?.status === 'done' && job.result) {
    const parts = [sourceCaption(job.result).label, mskDateTime(job.result.generated_at)];
    if (job.kind === 'kam') parts.unshift(`Период ${periodLabel(job.params)}`);
    if (job.reused) parts.push('из кэша');
    meta = parts.filter(Boolean).join(' · ');
  } else if (job?.kind === 'kam') {
    meta = `Период ${periodLabel(job.params)}`;
  }

  let body: ReactNode;
  if (requestError != null) {
    body = (
      <ProblemBlock
        title="Не удалось запросить сводку"
        text={errorMessage(requestError)}
        actions={
          <Button size="sm" variant="outline" onClick={() => controller.start({ mode: retryMode })}>
            <RefreshCw aria-hidden="true" />
            Повторить
          </Button>
        }
      />
    );
  } else if (pollExpired) {
    body = (
      <ProblemBlock
        title="Сводка формируется дольше 15 минут"
        text="Ожидание остановлено. Повторите позже — готовая сводка придёт из кэша — или сформируйте сводку по правилам."
        actions={
          <>
            <Button size="sm" onClick={() => controller.start({ mode: 'rules' })}>
              <ListChecks aria-hidden="true" />
              Сводка по правилам
            </Button>
            <Button size="sm" variant="outline" onClick={() => controller.start({ mode: retryMode })}>
              <RefreshCw aria-hidden="true" />
              Повторить
            </Button>
          </>
        }
      />
    );
  } else if (busy || !job || isPending(job.status)) {
    body = (
      <div className="flex flex-col gap-3">
        <p className="flex min-w-0 items-start gap-2 text-sm">
          <LoaderCircle className="mt-0.5 size-4 shrink-0 animate-spin text-primary" aria-hidden="true" />
          <span className="tabular text-pretty">
            {progressText(controller.creating ? null : job, controller.elapsedMs)}
          </span>
        </p>
        {controller.syncError ? (
          <p className="text-xs text-muted-foreground">Нет связи с сервером — пробуем снова.</p>
        ) : null}
        <div className="flex flex-col gap-2" aria-hidden="true">
          <Skeleton className="h-4 w-11/12 rounded-sm" />
          <Skeleton className="h-4 w-4/5 rounded-sm" />
          <Skeleton className="h-4 w-2/3 rounded-sm" />
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          {isLongQueue(job) ? (
            <Button size="sm" onClick={() => controller.start({ mode: 'rules' })}>
              <ListChecks aria-hidden="true" />
              Сводка по правилам
            </Button>
          ) : null}
          <Button size="sm" variant="outline" onClick={controller.close}>
            <EyeOff aria-hidden="true" />
            Скрыть
          </Button>
          <span className="min-w-0 text-pretty text-xs text-muted-foreground">
            Сводка дособерётся без вас: кнопка «Сводка ИИ» покажет её из кэша в течение часа.
          </span>
        </div>
      </div>
    );
  } else if (job.status === 'failed') {
    const view = errorView(job.error);
    body = (
      <ProblemBlock
        title={view.title}
        text={view.text}
        actions={
          <>
            {view.offerRules ? (
              <Button size="sm" onClick={() => controller.start({ mode: 'rules' })}>
                <ListChecks aria-hidden="true" />
                Сводка по правилам
              </Button>
            ) : null}
            {view.canRetry ? (
              <Button
                size="sm"
                variant={view.offerRules ? 'outline' : 'default'}
                onClick={() => controller.start({ mode: retryMode })}
              >
                <RefreshCw aria-hidden="true" />
                Повторить
              </Button>
            ) : null}
          </>
        }
      />
    );
  } else {
    body = <DoneBody job={job} controller={controller} onSelectKam={onSelectKam} />;
  }

  return (
    <section
      ref={controller.panelRef}
      tabIndex={-1}
      role="region"
      aria-label="Сводка ИИ"
      className={cn(
        'flex min-w-0 scroll-mt-4 flex-col gap-3 rounded-lg border border-primary-tint-2 bg-card p-4 shadow-card outline-none focus-visible:ring-2 focus-visible:ring-ring sm:p-5',
        className,
      )}
    >
      <header className="flex items-start gap-2.5">
        <span
          aria-hidden="true"
          className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary-tint text-primary"
        >
          <Sparkles className="size-4" />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="text-pretty text-base font-semibold leading-6">{title}</h2>
          {meta ? <p className="tabular text-pretty text-xs text-muted-foreground">{meta}</p> : null}
        </div>
        <Button
          variant="ghost"
          size="icon"
          className="-mr-1.5 -mt-1 size-8 shrink-0 text-muted-foreground"
          onClick={controller.close}
          aria-label={busy ? 'Скрыть сводку' : 'Закрыть сводку'}
        >
          <X aria-hidden="true" />
        </Button>
      </header>

      <p aria-live="polite" className="sr-only">
        {liveText(controller)}
      </p>

      {stale && !busy ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-md bg-status-warning-tint px-3 py-2 text-sm text-status-warning-deep">
          <span className="flex min-w-0 flex-1 items-start gap-2">
            <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            <span className="text-pretty">Фильтры изменились — сводка за прежние параметры</span>
          </span>
          <Button size="sm" variant="outline" onClick={() => controller.start({ mode: retryMode })}>
            <RefreshCw aria-hidden="true" />
            Обновить
          </Button>
        </div>
      ) : null}

      {body}
    </section>
  );
}

function ProblemBlock({ title, text, actions }: { title: string; text: string; actions: ReactNode }): ReactNode {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-start gap-2 rounded-md bg-muted px-3 py-2.5 text-sm">
        <TriangleAlert className="mt-0.5 size-4 shrink-0 text-status-warning-deep" aria-hidden="true" />
        <div className="min-w-0">
          <p className="text-pretty font-medium">{title}</p>
          <p className="text-pretty text-muted-foreground">{text}</p>
        </div>
      </div>
      <div className="flex flex-wrap gap-2">{actions}</div>
    </div>
  );
}

function DoneBody({
  job,
  controller,
  onSelectKam,
}: {
  job: AiSummaryJob;
  controller: AiSummaryController;
  onSelectKam?: (kamId: string) => void;
}): ReactNode {
  const result = job.result;
  if (!result) return null;
  const caption = sourceCaption(result);
  const sections = sectionList(result.sections, job.kind);
  const headline = sections.find((section) => section.key === 'headline');
  const rest = sections.filter((section) => section.key !== 'headline');
  const checks = checksCaption(result.checks);
  const people = job.kind === 'team' ? (result.people ?? []) : [];

  const copy = (): void => {
    copyText(toPlainText(job))
      .then(() => toastSuccess('Скопировано'))
      .catch((error: unknown) => toastError(error, { title: 'Не удалось скопировать' }));
  };

  return (
    <div className="flex flex-col gap-4">
      <p
        className={cn(
          'flex items-start gap-2 rounded-md px-3 py-2 text-xs',
          caption.isAi
            ? 'bg-status-progress-tint text-status-progress-deep'
            : 'bg-muted text-muted-foreground',
        )}
      >
        <Info className="mt-px size-3.5 shrink-0" aria-hidden="true" />
        <span className="text-pretty">{caption.note}</span>
      </p>

      {job.kind === 'kam' ? (
        <p className="-mt-2 text-pretty text-xs text-muted-foreground">
          Цифры — из разбора КАМа на вкладке «Просрочки» и из вкладки «Сводка для заказчика» с фильтром по
          этому КАМу.
        </p>
      ) : null}

      {sections.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          В сводке нет ни одной фразы: за период в отчёте нет данных для пересказа.
        </p>
      ) : null}

      {headline ? (
        <div>
          <h3 className="sr-only">{headline.title}</h3>
          <p className="text-pretty text-base font-medium leading-6 sm:text-lg sm:leading-7">
            {headline.items.join(' ')}
          </p>
        </div>
      ) : null}

      {rest.length > 0 ? (
        <div className="grid min-w-0 gap-x-6 gap-y-4 md:grid-cols-2">
          {rest.map((section) => (
            <SectionBlock key={section.key} section={section} />
          ))}
        </div>
      ) : null}

      {people.length > 0 ? <PeopleChips people={people} onSelectKam={onSelectKam} /> : null}

      <div className="flex flex-col gap-3 border-t border-border pt-3 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
        {checks ? (
          <p className="flex min-w-0 items-start gap-1.5 text-xs text-muted-foreground">
            <ShieldCheck className="mt-px size-3.5 shrink-0" aria-hidden="true" />
            <span className="text-pretty">{checks}</span>
          </p>
        ) : (
          <span />
        )}
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" onClick={copy}>
            <Copy aria-hidden="true" />
            Копировать
          </Button>
          <Button size="sm" variant="outline" onClick={() => controller.start({ mode: job.mode, force: true })}>
            <RefreshCw aria-hidden="true" />
            Обновить
          </Button>
          {result.source !== 'rules' ? (
            <Button size="sm" variant="outline" onClick={() => controller.start({ mode: 'rules' })}>
              <ListChecks aria-hidden="true" />
              По правилам
            </Button>
          ) : null}
          {job.mode === 'rules' ? (
            // после сбоя GigaChat взяли сводку по правилам — вернуться к GigaChat отсюда
            <Button size="sm" variant="outline" onClick={() => controller.start({ mode: 'auto' })}>
              <Sparkles aria-hidden="true" />
              Сформировать GigaChat
            </Button>
          ) : null}
          <Button size="sm" variant="ghost" onClick={controller.close}>
            Закрыть
          </Button>
        </div>
      </div>
    </div>
  );
}

function SectionBlock({ section }: { section: AiSectionView }): ReactNode {
  const dot = section.key === 'headline' ? null : SECTION_DOT[section.key];
  return (
    <div className="min-w-0">
      <h3 className="flex items-center gap-1.5 text-sm font-semibold">
        {dot ? <span className={cn('size-2 shrink-0 rounded-full', dot)} aria-hidden="true" /> : null}
        {section.title}
      </h3>
      <ul className="mt-1.5 flex flex-col gap-1.5 text-sm">
        {section.items.map((item, index) => (
          <li key={index} className="flex min-w-0 gap-2">
            <span className="text-muted-foreground" aria-hidden="true">
              —
            </span>
            <span className="min-w-0 text-pretty break-words">{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function PeopleChips({
  people,
  onSelectKam,
}: {
  people: AiSummaryPerson[];
  onSelectKam?: (kamId: string) => void;
}): ReactNode {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-xs text-muted-foreground">
        {onSelectKam ? 'КАМы в сводке — разбор просрочек:' : 'КАМы в сводке:'}
      </span>
      <ul className="flex flex-wrap gap-1.5">
        {people.map((person) => (
          <li key={person.kam_id}>
            {onSelectKam ? (
              <button
                type="button"
                onClick={() => onSelectKam(person.kam_id)}
                className="inline-flex max-w-full items-center rounded-full border border-border bg-card px-2.5 py-1 text-xs font-medium transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <span className="truncate">{person.name}</span>
              </button>
            ) : (
              <span className="inline-flex max-w-full items-center rounded-full border border-border px-2.5 py-1 text-xs">
                <span className="truncate">{person.name}</span>
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
