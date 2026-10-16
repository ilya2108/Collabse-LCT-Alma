import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Hourglass, Info, Loader2, TriangleAlert, Undo2 } from 'lucide-react';
import { motion } from 'motion/react';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { ImpactSummary, OperationsDiff } from '@/components/workflow/OperationsDiff';
import { useOnboarding } from '@/features/onboarding';
import {
  LocalEmptyState,
  LocalErrorState,
  LocalLoadingState,
  LocalPageHeader,
  LocalSegmented,
} from '@/components/wp5/PageChrome';
import {
  approveWorkflowChange,
  listWorkflowChanges,
  rejectWorkflowChange,
} from '@/shared/api/endpoints/workflowChanges';
import { getWorkflow, listWorkflows } from '@/shared/api/endpoints/workflows';
import { errorMessage, isApiError } from '@/shared/api/errors';
import type {
  WorkflowChangeRecord,
  WorkflowChangeStatus,
  WorkflowMigrationReport,
  WorkflowSchema,
  WorkflowType,
} from '@/shared/api/types';
import { useAuth } from '@/shared/auth/AuthContext';
import { WORKFLOW_TYPE_LABELS } from '@/shared/auth/permissions';
import { cn } from '@/shared/lib/cn';
import { formatDateTime } from '@/shared/lib/format';
import { staggerContainer, staggerItem } from '@/shared/lib/motion';
import { toastError, toastSuccess } from '@/shared/lib/toast';
import { useSseInvalidate } from '@/shared/lib/useSseInvalidate';
import { Alert, AlertDescription, AlertTitle } from '@/shared/ui/alert';
import { Button } from '@/shared/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/ui/dialog';
import { Label } from '@/shared/ui/label';
import { Separator } from '@/shared/ui/separator';
import { Textarea } from '@/shared/ui/textarea';

/**
 * Согласования workflow (redesign.md §6.9): входящие пакеты изменений схем —
 * карточки со строкой автор+дата+workflow, диффом-плашками (как impact-preview
 * §6.6) и migration impact; approve применяет схему и мигрирует заявки
 * атомарно, reject возвращает автору с причиной.
 *
 * - Пакеты читаются по процессам с правом `workflow:history`, решение (approve /
 *   reject) — только в процессах с `workflow:approve` (F2).
 * - Отчёт о переводе (api-contract.md §5.3–5.4, workflow-engine.md §6.8): после
 *   approve — тост «перенесено N заявок за X, сводных уведомлений K»; у применённого
 *   пакета — та же строка и разбивка `by_status` из сохранённого `migration_report`.
 * - Перенос от 1000 заявок — предупреждение в диалоге: одна транзакция, переходы
 *   по воронке на это время встают в очередь.
 * - 409 `workflow_busy` (по воронке идут переходы): клиент сам ждёт по Retry-After
 *   до 10 с (shared/api/idempotency.ts), диалог показывает, что идёт ожидание; не
 *   дождались — пакет остаётся на согласовании, диалог открыт для повтора.
 */

type FilterStatus = Extract<WorkflowChangeStatus, 'pending' | 'applied' | 'rejected'>;

interface ChangesData {
  schemas: Record<string, WorkflowSchema>;
  wfNames: Record<string, string>;
  wfTypes: Record<string, WorkflowType>;
  changes: WorkflowChangeRecord[];
}

/** С этого числа переносимых заявок диалог предупреждает о транзакции и очереди переходов. */
const LARGE_MIGRATION_THRESHOLD = 1000;
/** Через сколько мс применения показать подсказку о долгом ожидании. */
const SLOW_APPLY_HINT_MS = 2000;

const STATUS_META: Record<string, { className: string; label: string }> = {
  pending: {
    className: 'bg-status-warning-tint text-status-warning-deep',
    label: 'Ждёт согласования',
  },
  approved: {
    className: 'bg-status-progress-tint text-status-progress-deep',
    label: 'Одобрен',
  },
  applied: {
    className: 'bg-status-success-tint text-status-success-deep',
    label: 'Применён',
  },
  rejected: {
    className: 'bg-status-danger-tint text-status-danger-deep',
    label: 'Отклонён',
  },
  failed: {
    className: 'bg-status-danger-tint text-status-danger-deep',
    label: 'Ошибка применения',
  },
};

const countFormatter = new Intl.NumberFormat('ru-RU');

/** «10 000» — разряды неразрывными пробелами (ux.md §4.4). */
function formatCount(value: number): string {
  return countFormatter.format(value);
}

/** Длительность применения: до секунды — «850 мс», дальше — «1,2 с». */
function formatDuration(ms: number): string {
  const rounded = Math.round(ms);
  if (rounded < 1) return 'меньше 1 мс';
  if (rounded < 1000) return `${rounded} мс`;
  return `${(rounded / 1000).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} с`;
}

/**
 * «10 000 за 330 мс»: время — от захвата лока воронки до COMMIT (`timings.apply_ms`),
 * одинаковое в тосте и в карточке; у пакетов без замеров — только число.
 */
function migratedFacts(report: WorkflowMigrationReport): string {
  const count = formatCount(report.migrated_requests);
  return report.timings ? `${count} за ${formatDuration(report.timings.apply_ms)}` : count;
}

function approveToastDescription(report: WorkflowMigrationReport | null | undefined): string {
  const boards = 'Все открытые доски обновятся автоматически.';
  if (!report || report.migrated_requests === 0) {
    return `Перенос заявок не потребовался. ${boards}`;
  }
  const parts = [`Перенесено заявок: ${migratedFacts(report)}.`];
  const digests = report.digest_notifications ?? 0;
  if (digests > 0) {
    parts.push(`Сводных уведомлений: ${formatCount(digests)} — по одному на получателя.`);
  }
  parts.push(boards);
  return parts.join(' ');
}

function isWorkflowBusy(error: unknown): boolean {
  return isApiError(error) && error.code === 'workflow_busy';
}

/**
 * Пакет точно не применён и остался на согласовании — повтор из того же диалога
 * безопасен (тот же Idempotency-Key). Остальные ошибки окончательны: пакет уже
 * обработан или помечен failed — диалог закрывается, список перечитывается.
 */
function isRetryableApplyError(error: unknown): boolean {
  if (!isApiError(error)) return false;
  return (
    error.code === 'workflow_busy' ||
    error.code === 'network_error' ||
    error.code === 'idempotency_in_progress' ||
    error.status === 502 ||
    error.status === 503 ||
    error.status === 504
  );
}

/**
 * true, пока мутация, начатая в `startedAt`, выполняется дольше `delayMs`
 * (ожидание `workflow_busy` или перенос большого объёма).
 */
function useSlowPending(pending: boolean, startedAt: number, delayMs: number): boolean {
  const [slowSince, setSlowSince] = useState(0);
  useEffect(() => {
    if (!pending || startedAt === 0) return undefined;
    const timer = window.setTimeout(
      () => setSlowSince(startedAt),
      Math.max(0, startedAt + delayMs - Date.now()),
    );
    return () => window.clearTimeout(timer);
  }, [pending, startedAt, delayMs]);
  return pending && startedAt !== 0 && slowSince === startedAt;
}

async function loadChanges(
  status: FilterStatus,
  canRead: (type: WorkflowType) => boolean,
  signal?: AbortSignal,
): Promise<ChangesData> {
  const workflows = (await listWorkflows(signal)).filter((workflow) => canRead(workflow.type));
  const schemas: Record<string, WorkflowSchema> = {};
  const wfNames: Record<string, string> = {};
  const wfTypes: Record<string, WorkflowType> = {};
  const changes: WorkflowChangeRecord[] = [];
  await Promise.all(
    workflows.map(async (workflow) => {
      wfNames[workflow.id] = workflow.name;
      wfTypes[workflow.id] = workflow.type;
      const [schema, list] = await Promise.all([
        getWorkflow(workflow.id, signal),
        listWorkflowChanges(workflow.id, status, signal),
      ]);
      schemas[workflow.id] = schema;
      changes.push(...list);
    }),
  );
  changes.sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
  return { schemas, wfNames, wfTypes, changes };
}

/** Итог применённого пакета: дата, сколько заявок перенесено и за сколько, разбивка. */
function AppliedSummary({ change }: { change: WorkflowChangeRecord }): ReactNode {
  const report = change.migration_report;
  let migrated = '';
  if (report) {
    migrated =
      report.migrated_requests > 0
        ? ` · перенесено заявок: ${migratedFacts(report)}`
        : ' · перенос заявок не потребовался';
  }
  const digests = report?.digest_notifications ?? 0;
  return (
    <div className="space-y-1">
      <p className="text-sm text-muted-foreground">
        Применён {formatDateTime(change.applied_at ?? change.decided_at ?? null)}
        {migrated}
      </p>
      {report && report.by_status.length > 0 ? (
        <ul className="flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-muted-foreground">
          {report.by_status.map((row) => (
            <li key={`${row.from}→${row.to}`} className="min-w-0 break-words">
              «{row.from}» → «{row.to}»: <span className="tabular">{formatCount(row.count)}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {digests > 0 ? (
        <p className="text-xs text-muted-foreground">
          Сводных уведомлений: <span className="tabular">{formatCount(digests)}</span> — по
          одному на получателя
        </p>
      ) : null}
    </div>
  );
}

export function AdminApprovalsPage(): ReactNode {
  const queryClient = useQueryClient();
  const { hasPermission, workflowTypesWith } = useAuth();
  // Чек-лист онбординга §7.5: открытие согласований (no-op без провайдера).
  const { completeChecklistItem } = useOnboarding();
  useEffect(() => {
    completeChecklistItem('open-approvals');
  }, [completeChecklistItem]);
  const [statusFilter, setStatusFilter] = useState<FilterStatus>('pending');
  const [approveFor, setApproveFor] = useState<WorkflowChangeRecord | null>(null);
  const [busyMessage, setBusyMessage] = useState<string | null>(null);
  const [rejectFor, setRejectFor] = useState<WorkflowChangeRecord | null>(null);
  const [rejectReason, setRejectReason] = useState('');

  // процессы, пакеты которых видны (workflow:history), — часть ключа запроса:
  // смена прав на лету (AccessChangeListener) перечитывает список
  const readable = workflowTypesWith('workflow:history');
  const readableKey = readable.join(',');

  const query = useQuery({
    queryKey: ['workflow-changes-admin', statusFilter, readableKey],
    queryFn: ({ signal }) =>
      loadChanges(statusFilter, (type) => readable.includes(type), signal),
    staleTime: 15_000,
  });

  const sseKeys = useMemo(
    () => [['workflow-changes-admin'], ['workflow-changes'], ['workflow-schema'], ['workflows']],
    [],
  );
  useSseInvalidate('workflow.changed', sseKeys);

  const invalidateAll = (): void => {
    void queryClient.invalidateQueries({ queryKey: ['workflow-changes-admin'] });
    void queryClient.invalidateQueries({ queryKey: ['workflow-changes'] });
    void queryClient.invalidateQueries({ queryKey: ['workflow-schema'] });
    void queryClient.invalidateQueries({ queryKey: ['workflows'] });
    void queryClient.invalidateQueries({ queryKey: ['board-requests'] });
  };

  const approveMutation = useMutation({
    mutationFn: (changeId: string) => approveWorkflowChange(changeId),
    meta: { silent: true },
    onMutate: () => setBusyMessage(null),
    onSuccess: (record) => {
      setApproveFor(null);
      toastSuccess('Схема применена', approveToastDescription(record.migration_report));
      invalidateAll();
    },
    onError: (error) => {
      if (isWorkflowBusy(error)) {
        // клиент уже ждал по Retry-After (до 10 с) — переходы по воронке не закончились;
        // пакет не применён и остаётся на согласовании: объясняем в открытом диалоге
        setBusyMessage(errorMessage(error));
      } else {
        toastError(error, { title: 'Не удалось применить пакет' });
        if (!isRetryableApplyError(error)) setApproveFor(null);
      }
      // 409: схема изменилась после создания пакета — показываем свежие данные
      invalidateAll();
    },
  });
  const slowApply = useSlowPending(
    approveMutation.isPending,
    approveMutation.submittedAt,
    SLOW_APPLY_HINT_MS,
  );

  const closeApprove = (): void => {
    setApproveFor(null);
    setBusyMessage(null);
  };

  const rejectMutation = useMutation({
    mutationFn: ({ changeId, reason }: { changeId: string; reason: string }) =>
      rejectWorkflowChange(changeId, reason),
    meta: { silent: true },
    onSuccess: () => {
      setRejectFor(null);
      setRejectReason('');
      toastSuccess('Пакет возвращён автору');
      invalidateAll();
    },
    onError: (error) => toastError(error, { title: 'Не удалось отклонить пакет' }),
  });

  let body: ReactNode;
  if (query.isLoading) {
    body = <LocalLoadingState rows={8} />;
  } else if (query.isError) {
    body = <LocalErrorState error={query.error} onRetry={() => void query.refetch()} />;
  } else {
    const data = query.data as ChangesData;
    if (data.changes.length === 0) {
      body = (
        <div className="rounded-lg border bg-card shadow-card">
          <LocalEmptyState
            illustration="registry-empty"
            title="Согласований нет"
            description="Изменения схем приходят из конструктора процессов: опасные операции (переименование и удаление этапов) всегда требуют вашего решения."
          />
        </div>
      );
    } else {
      body = (
        <motion.ul
          variants={staggerContainer}
          initial="hidden"
          animate="visible"
          className="space-y-3"
        >
          {data.changes.map((change) => {
            const meta = STATUS_META[change.status] ?? {
              className: 'bg-muted text-muted-foreground',
              label: change.status,
            };
            const wfType = data.wfTypes[change.workflow_id];
            const canDecide = wfType ? hasPermission('workflow:approve', wfType) : false;
            // у применённого пакета прогноз impact устарел — показываем фактический отчёт
            const showForecast = !(change.status === 'applied' && change.migration_report);
            return (
              <motion.li
                key={change.id}
                variants={staggerItem}
                className="rounded-lg border bg-card p-4 shadow-card"
              >
                <div className="space-y-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="rounded-sm bg-primary-tint-2 px-2 py-0.5 text-xs font-semibold text-primary-active">
                      {wfType ? WORKFLOW_TYPE_LABELS[wfType] : 'WF'}
                    </span>
                    <span className="text-sm font-semibold">
                      {data.wfNames[change.workflow_id] ?? change.workflow_id}
                    </span>
                    <span
                      className={cn(
                        'rounded-sm px-2 py-0.5 text-xs font-medium',
                        meta.className,
                      )}
                    >
                      {meta.label}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {change.author ? `${change.author.full_name} · ` : ''}
                      {formatDateTime(change.created_at)}
                    </span>
                  </div>
                  {change.comment ? (
                    <p className="rounded-md bg-muted px-3 py-2 text-sm">
                      Комментарий автора: {change.comment}
                    </p>
                  ) : null}
                  <OperationsDiff
                    operations={change.operations}
                    schema={data.schemas[change.workflow_id]}
                    impact={change.impact}
                  />
                  {showForecast ? <ImpactSummary impact={change.impact} /> : null}
                  {change.status === 'rejected' && change.decision_comment ? (
                    <Alert variant="warning">
                      <TriangleAlert aria-hidden="true" />
                      <AlertTitle className="text-balance">
                        Причина отклонения: {change.decision_comment}
                      </AlertTitle>
                    </Alert>
                  ) : null}
                  {change.status === 'applied' ? <AppliedSummary change={change} /> : null}
                  {change.status === 'pending' && canDecide ? (
                    <>
                      <Separator />
                      <div className="flex flex-wrap gap-2">
                        <Button
                          onClick={() => {
                            setBusyMessage(null);
                            setApproveFor(change);
                          }}
                        >
                          <Check aria-hidden="true" /> Согласовать и опубликовать
                        </Button>
                        <Button
                          variant="outline"
                          onClick={() => {
                            setRejectFor(change);
                            setRejectReason('');
                          }}
                        >
                          <Undo2 aria-hidden="true" /> Вернуть с комментарием
                        </Button>
                      </div>
                    </>
                  ) : null}
                  {change.status === 'pending' && !canDecide ? (
                    <p className="text-xs text-pretty text-muted-foreground">
                      Решение по пакету принимает пользователь с правом «Согласование и
                      публикация изменений схемы» в процессе{' '}
                      {wfType ? WORKFLOW_TYPE_LABELS[wfType] : 'этой воронки'}.
                    </p>
                  ) : null}
                </div>
              </motion.li>
            );
          })}
        </motion.ul>
      );
    }
  }

  const affectedTotal = approveFor?.impact?.affected_requests_total ?? 0;

  return (
    <>
      <LocalPageHeader
        title="Согласования процессов"
        subtitle="Пакеты изменений схем из конструктора: дифф, влияние на заявки, решение администратора"
      />
      <div data-tour="admin-approvals" className="mb-3 rounded-lg border bg-card p-3 shadow-card">
        <LocalSegmented<FilterStatus>
          aria-label="Статус пакетов"
          value={statusFilter}
          onChange={setStatusFilter}
          options={[
            { value: 'pending', label: 'На согласовании' },
            { value: 'applied', label: 'Применённые' },
            { value: 'rejected', label: 'Отклонённые' },
          ]}
        />
      </div>
      {body}

      {approveFor ? (
        <Dialog
          open
          onOpenChange={(next) => (!next && !approveMutation.isPending ? closeApprove() : undefined)}
        >
          <DialogContent className="max-h-[85vh] gap-4 overflow-y-auto sm:max-w-[640px]">
            <DialogHeader>
              <DialogTitle>Согласовать и опубликовать изменения?</DialogTitle>
            </DialogHeader>
            <Alert variant="warning">
              <TriangleAlert aria-hidden="true" />
              <AlertTitle>Схема применится сразу для всех — версий нет</AlertTitle>
              <p className="col-start-2 text-sm text-pretty">
                Заявки из удаляемых этапов будут перенесены атомарно, в историю каждой добавится
                запись; получатели уведомлений узнают о переносе из одной сводки, а не из
                сообщения о каждой заявке.
              </p>
            </Alert>
            {affectedTotal >= LARGE_MIGRATION_THRESHOLD ? (
              <Alert variant="info">
                <Info aria-hidden="true" />
                <AlertTitle className="line-clamp-none text-balance">
                  Крупный перенос — одной транзакцией
                </AlertTitle>
                <AlertDescription className="text-pretty">
                  Перенос выполняется одной транзакцией: либо все заявки переедут, либо ни одна.
                  На это время (обычно меньше секунды) переходы по этой воронке встанут в
                  очередь — данные не потеряются. Ответственные получат по одному сводному
                  уведомлению.
                </AlertDescription>
              </Alert>
            ) : null}
            <OperationsDiff
              operations={approveFor.operations}
              schema={query.data?.schemas[approveFor.workflow_id]}
              impact={approveFor.impact}
            />
            <ImpactSummary impact={approveFor.impact} />
            {busyMessage ? (
              <Alert variant="warning">
                <Hourglass aria-hidden="true" />
                <AlertTitle className="line-clamp-none text-balance">
                  Схема не применена — воронка занята переходами
                </AlertTitle>
                <AlertDescription className="text-pretty">
                  <p>{busyMessage}</p>
                  <p>Пакет остался на согласовании, заявки не тронуты — повтор безопасен.</p>
                </AlertDescription>
              </Alert>
            ) : null}
            {slowApply ? (
              <p
                role="status"
                aria-live="polite"
                className="flex items-start gap-2 text-sm text-muted-foreground"
              >
                <Hourglass className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                <span className="text-pretty">
                  Применение идёт дольше обычного: переносим заявки или ждём, пока завершатся
                  уже начатые переходы по воронке (до 10 с). Нажимать ещё раз не нужно — окно
                  закроется само.
                </span>
              </p>
            ) : null}
            <DialogFooter>
              <Button
                variant="outline"
                disabled={approveMutation.isPending}
                onClick={closeApprove}
              >
                Отмена
              </Button>
              <Button
                disabled={approveMutation.isPending}
                onClick={() => approveMutation.mutate(approveFor.id)}
              >
                {approveMutation.isPending ? (
                  <Loader2 className="animate-spin" aria-hidden="true" />
                ) : (
                  <Check aria-hidden="true" />
                )}
                {approveMutation.isPending
                  ? 'Применяем…'
                  : busyMessage
                    ? 'Повторить применение'
                    : 'Применить схему'}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      ) : null}

      {rejectFor ? (
        <Dialog
          open
          onOpenChange={(next) => (!next && !rejectMutation.isPending ? setRejectFor(null) : undefined)}
        >
          <DialogContent className="sm:max-w-[480px]">
            <DialogHeader>
              <DialogTitle>Вернуть пакет автору</DialogTitle>
            </DialogHeader>
            <div className="space-y-1.5">
              <Label htmlFor="reject-reason">
                Причина обязательна — автор увидит её в конструкторе и в уведомлении
              </Label>
              <Textarea
                id="reject-reason"
                value={rejectReason}
                onChange={(event) => setRejectReason(event.target.value)}
                rows={3}
                maxLength={500}
                placeholder="Что нужно поправить…"
              />
            </div>
            <DialogFooter>
              <Button
                variant="outline"
                disabled={rejectMutation.isPending}
                onClick={() => setRejectFor(null)}
              >
                Отмена
              </Button>
              <Button
                variant="destructive"
                disabled={rejectReason.trim().length === 0 || rejectMutation.isPending}
                onClick={() =>
                  rejectMutation.mutate({ changeId: rejectFor.id, reason: rejectReason.trim() })
                }
              >
                {rejectMutation.isPending ? (
                  <Loader2 className="animate-spin" aria-hidden="true" />
                ) : null}
                Отклонить
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      ) : null}
    </>
  );
}
