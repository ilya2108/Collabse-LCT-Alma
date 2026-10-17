import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowRight,
  ArrowRightLeft,
  FilePlus2,
  Loader2,
  Paperclip,
  Pencil,
  Plug,
  Shuffle,
  Undo2,
  UserRoundCog,
} from 'lucide-react';
import { useMemo, useRef, useState, type ReactNode } from 'react';
import { downloadFile, formatFileSize } from '@/shared/api/endpoints/files';
import {
  REQUEST_FILE_ACCEPT,
  listRequestHistory,
  requestFileProblem,
  uploadRequestFile,
  type RequestFile,
  type RequestHistoryEntry,
} from '@/shared/api/endpoints/requests';
import { listIntegrationEvents } from '@/shared/api/endpoints/integrationEvents';
import type { IntegrationEventRecord, WorkflowType } from '@/shared/api/types';
import { useAuth } from '@/shared/auth/AuthContext';
import type { SemanticStatus } from '@/shared/config/tokens';
import { formatDateTime, formatRelative } from '@/shared/lib/format';
import { cn } from '@/shared/lib/cn';
import { toastError, toastSuccess } from '@/shared/lib/toast';
import { Button } from '@/shared/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/shared/ui/dialog';
import { Timeline, type TimelineItem } from '@/shared/ui/timeline';
import { SemanticBadge } from './badges';
import { requestFileIcon } from './RequestFiles';
import { ErrorPanel, LoadingPanel } from './StatePanels';

/**
 * Таймлайн истории заявки (ux.md §8.1, redesign.md §6.4): события с иконками
 * по типам на компоненте Timeline (§3.7) — переходы, возвраты (danger «↩»),
 * смены ответственного, миграции схемы, события интеграций LMS/CMS
 * (admin/observer) с кнопкой «показать JSON». Фильтр — чипы над лентой.
 * Файлы в статусах (ФТ3): под переходом — его комментарий и приложенные файлы
 * (скачивание по клику); с правом `requests:files` к прошлому переходу можно
 * доприложить файл («скан к „Договор подписан“»).
 */

/** Файлы перехода: чипы с именем и размером, клик — скачивание через backend. */
function HistoryFiles({ files }: { files: RequestFile[] }): ReactNode {
  if (files.length === 0) return null;
  const download = async (file: RequestFile): Promise<void> => {
    try {
      await downloadFile(file.id, file.file_name);
    } catch (error) {
      toastError(error, { title: 'Не удалось скачать файл' });
    }
  };
  return (
    <ul className="m-0 mt-1.5 flex list-none flex-wrap gap-1.5 p-0" aria-label="Файлы перехода">
      {files.map((file) => {
        const Icon = requestFileIcon(file.file_name);
        return (
          <li key={file.id} className="min-w-0 max-w-full">
            <button
              type="button"
              onClick={() => void download(file)}
              title={`${file.uploaded_by_name ?? ''} · ${formatDateTime(file.created_at)}`}
              className="flex max-w-full items-center gap-1.5 rounded-md border bg-card px-2 py-1 text-xs text-foreground transition-colors hover:bg-primary-tint"
            >
              <Icon className="size-3.5 shrink-0 text-primary" aria-hidden="true" />
              <span className="min-w-0 truncate">{file.file_name}</span>
              <span className="shrink-0 text-muted-foreground">
                {formatFileSize(file.size_bytes)}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

/** «＋ файл» под переходом: доприложить файл к уже выполненному переходу. */
function AttachToTransition({
  requestId,
  historyId,
}: {
  requestId: string;
  historyId: string;
}): ReactNode {
  const queryClient = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);

  const upload = async (list: FileList | null): Promise<void> => {
    const files = Array.from(list ?? []);
    if (inputRef.current) inputRef.current.value = '';
    if (files.length === 0) return;
    setBusy(true);
    try {
      for (const file of files) {
        const problem = requestFileProblem(file);
        if (problem) {
          toastError(new Error(problem), { title: 'Файл не подходит' });
          continue;
        }
        try {
          await uploadRequestFile(requestId, file, { stageHistoryId: historyId });
          toastSuccess(`Файл «${file.name}» приложен к переходу`);
        } catch (error) {
          toastError(error, { title: `Не удалось загрузить «${file.name}»` });
        }
      }
    } finally {
      setBusy(false);
      void queryClient.invalidateQueries({ queryKey: ['request-history', requestId] });
      void queryClient.invalidateQueries({ queryKey: ['request-files', requestId] });
    }
  };

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={REQUEST_FILE_ACCEPT.join(',')}
        className="sr-only"
        tabIndex={-1}
        aria-hidden="true"
        onChange={(event) => void upload(event.target.files)}
      />
      <Button
        variant="link"
        size="sm"
        className="h-auto p-0 text-xs"
        disabled={busy}
        onClick={() => inputRef.current?.click()}
      >
        {busy ? (
          <Loader2 className="size-3! animate-spin" aria-hidden="true" />
        ) : (
          <Paperclip className="size-3!" aria-hidden="true" />
        )}
        Приложить файл
      </Button>
    </>
  );
}

type TimelineFilter = 'all' | 'stages' | 'assign' | 'integrations';

interface Entry extends TimelineItem {
  at: string;
  filter: Exclude<TimelineFilter, 'all'>;
}

const INTEGRATION_STATUS: Record<
  IntegrationEventRecord['status'],
  { label: string; tone: SemanticStatus }
> = {
  pending: { label: 'в очереди', tone: 'progress' },
  retrying: { label: 'повтор', tone: 'warning' },
  delivered: { label: 'доставлено', tone: 'success' },
  processed: { label: 'обработано', tone: 'success' },
  dead: { label: 'ошибка (DLQ)', tone: 'danger' },
  skipped: { label: 'пропущено', tone: 'draft' },
};

function Arrow(): ReactNode {
  return <ArrowRight className="inline size-3 align-middle text-muted-foreground" aria-label="в" />;
}

/**
 * Имена этапов берутся из СНАПШОТОВ события (StatusRef внутри записи истории),
 * а не резолвятся по живой схеме workflow: конструктор может переименовать или
 * удалить этап, а таймлайн обязан показывать, как этап назывался в момент
 * перехода. Поэтому здесь нигде нет lookup'а в текущий WorkflowSchema.
 */
function historyEntry(item: RequestHistoryEntry): Entry {
  const actorName = item.actor?.full_name ?? 'Система';
  const actor = <span className="text-muted-foreground"> · {actorName}</span>;
  const comment = item.comment ? (
    <span className="text-muted-foreground">«{item.comment}»</span>
  ) : undefined;

  switch (item.kind) {
    case 'created':
      return {
        id: `h-${item.id}`,
        at: item.created_at,
        filter: 'stages',
        icon: FilePlus2,
        tone: 'success',
        title: (
          <>
            <span className="font-medium">Заявка создана</span>
            {item.to_status ? <> в этапе «{item.to_status.name}»</> : null}
            {actor}
          </>
        ),
        time: formatRelative(item.created_at),
      };
    case 'transition':
      return {
        id: `h-${item.id}`,
        at: item.created_at,
        filter: 'stages',
        icon: item.is_return ? Undo2 : ArrowRightLeft,
        tone: item.is_return ? 'danger' : 'progress',
        title: (
          <>
            {item.is_return ? (
              <span className="font-medium text-status-danger-deep">↩ Возврат: </span>
            ) : null}
            {item.from_status?.name ?? '—'} <Arrow /> {item.to_status?.name ?? '—'}
            {actor}
          </>
        ),
        time: formatRelative(item.created_at),
        content: comment,
      };
    case 'migrated':
      return {
        id: `h-${item.id}`,
        at: item.created_at,
        filter: 'stages',
        icon: Shuffle,
        tone: 'warning',
        title: (
          <>
            <SemanticBadge status="warning">миграция схемы</SemanticBadge>{' '}
            {item.from_status?.name ?? '—'} <Arrow /> {item.to_status?.name ?? '—'}
          </>
        ),
        time: formatRelative(item.created_at),
        content: comment,
      };
    case 'assign':
      return {
        id: `h-${item.id}`,
        at: item.created_at,
        filter: 'assign',
        icon: UserRoundCog,
        tone: 'progress',
        title: (
          <>
            <span className="font-medium">Смена ответственного</span>
            {actor}
          </>
        ),
        time: formatRelative(item.created_at),
        content: comment,
      };
    case 'field_change':
    default:
      return {
        id: `h-${item.id}`,
        at: item.created_at,
        filter: 'assign',
        icon: Pencil,
        tone: 'draft',
        title: (
          <>
            Изменены поля заявки
            {actor}
          </>
        ),
        time: formatRelative(item.created_at),
      };
  }
}

/** Строки, которыми заявка входит в этап: под ними — файлы этого статуса (ФТ3). */
const STAGE_ENTRY_KINDS: ReadonlySet<RequestHistoryEntry['kind']> = new Set([
  'created',
  'transition',
  'migrated',
]);

function withStageFiles(entry: Entry, item: RequestHistoryEntry, attach: ReactNode): Entry {
  const files = item.files ?? [];
  if (!STAGE_ENTRY_KINDS.has(item.kind) || (files.length === 0 && !attach)) return entry;
  return {
    ...entry,
    content: (
      <>
        {entry.content ? <div>{entry.content}</div> : null}
        <HistoryFiles files={files} />
        {attach ? <div className="mt-1">{attach}</div> : null}
      </>
    ),
  };
}

function integrationEntry(
  event: IntegrationEventRecord,
  onShowJson: (event: IntegrationEventRecord) => void,
): Entry {
  const direction = event.direction === 'outbound' ? '→' : '←';
  const status = INTEGRATION_STATUS[event.status];
  return {
    id: `i-${event.id}`,
    at: event.created_at,
    filter: 'integrations',
    icon: Plug,
    tone: event.status === 'dead' ? 'danger' : 'talent',
    title: (
      <span className="inline-flex flex-wrap items-center gap-1.5">
        <span className="font-medium">
          {direction} {event.system.toUpperCase()}
        </span>
        <code className="rounded-sm bg-muted px-1 py-0.5 text-xs">{event.event_type}</code>
        <SemanticBadge status={status.tone}>{status.label}</SemanticBadge>
      </span>
    ),
    time: formatRelative(event.created_at),
    content: (
      <Button variant="link" size="sm" className="h-auto p-0 text-xs" onClick={() => onShowJson(event)}>
        показать JSON
      </Button>
    ),
  };
}

/** Чип фильтра ленты. */
function FilterChip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}): ReactNode {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        'rounded-full border px-3 py-1 text-xs font-medium transition-colors duration-150',
        active
          ? 'border-transparent bg-primary-tint-2 text-primary-active'
          : 'border-border bg-card text-muted-foreground hover:bg-primary-tint hover:text-foreground',
      )}
    >
      {children}
    </button>
  );
}

export function RequestTimeline({
  requestId,
  workflowType,
}: {
  requestId: string;
  /** Процесс заявки: «Приложить файл» к переходу — с правом `requests:files` в нём. */
  workflowType?: WorkflowType;
}): ReactNode {
  const { hasPermission } = useAuth();
  const canAttach = Boolean(workflowType) && hasPermission('requests:files', workflowType);
  const [filter, setFilter] = useState<TimelineFilter>('all');
  const [jsonEvent, setJsonEvent] = useState<IntegrationEventRecord | null>(null);
  const canSeeIntegrationLog = hasPermission('admin:audit');

  const historyQuery = useQuery({
    queryKey: ['request-history', requestId],
    queryFn: ({ signal }) => listRequestHistory(requestId, signal),
  });
  const integrationQuery = useQuery({
    queryKey: ['request-integration-events', requestId],
    queryFn: ({ signal }) =>
      listIntegrationEvents({ entity_id: requestId }, { limit: 50, signal }),
    enabled: canSeeIntegrationLog,
  });

  const entries = useMemo(() => {
    const history = (historyQuery.data?.items ?? []).map((item) =>
      withStageFiles(
        historyEntry(item),
        item,
        canAttach && item.kind !== 'migrated' ? (
          <AttachToTransition requestId={requestId} historyId={item.id} />
        ) : null,
      ),
    );
    const integrations = (integrationQuery.data?.items ?? []).map((event) =>
      integrationEntry(event, setJsonEvent),
    );
    return [...history, ...integrations].sort((a, b) => (a.at < b.at ? 1 : -1));
  }, [historyQuery.data, integrationQuery.data, canAttach, requestId]);

  if (historyQuery.isLoading) return <LoadingPanel rows={5} />;
  if (historyQuery.isError) {
    return (
      <ErrorPanel
        error={historyQuery.error}
        title="Не удалось загрузить историю"
        onRetry={() => void historyQuery.refetch()}
      />
    );
  }

  const visible = entries.filter((entry) => filter === 'all' || entry.filter === filter);

  const filters: Array<{ value: TimelineFilter; label: string }> = [
    { value: 'all', label: 'Все' },
    { value: 'stages', label: 'Этапы' },
    { value: 'assign', label: 'Изменения' },
    ...(canSeeIntegrationLog
      ? [{ value: 'integrations' as const, label: 'Интеграции' }]
      : []),
  ];

  return (
    <>
      <Timeline
        items={visible}
        filter={filters.map((f) => (
          <FilterChip key={f.value} active={filter === f.value} onClick={() => setFilter(f.value)}>
            {f.label}
          </FilterChip>
        ))}
      />
      {visible.length === 0 ? (
        <p className="text-sm text-muted-foreground">Событий этого типа пока нет</p>
      ) : null}

      <Dialog open={jsonEvent !== null} onOpenChange={(open) => { if (!open) setJsonEvent(null); }}>
        <DialogContent className="sm:max-w-[640px]" aria-describedby={undefined}>
          <DialogHeader>
            <DialogTitle>
              {jsonEvent ? `${jsonEvent.event_type} (${jsonEvent.system.toUpperCase()})` : ''}
            </DialogTitle>
          </DialogHeader>
          <pre className="max-h-[420px] overflow-auto rounded-md bg-sidebar p-4 text-xs text-sidebar-muted">
            {jsonEvent ? JSON.stringify(jsonEvent.payload, null, 2) : ''}
          </pre>
        </DialogContent>
      </Dialog>
    </>
  );
}
