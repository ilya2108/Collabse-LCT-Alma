import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Loader2, TriangleAlert, Undo2 } from 'lucide-react';
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
import type {
  WorkflowChangeRecord,
  WorkflowChangeStatus,
  WorkflowSchema,
} from '@/shared/api/types';
import { cn } from '@/shared/lib/cn';
import { formatDateTime } from '@/shared/lib/format';
import { staggerContainer, staggerItem } from '@/shared/lib/motion';
import { toastError, toastSuccess } from '@/shared/lib/toast';
import { useSseInvalidate } from '@/shared/lib/useSseInvalidate';
import { Alert, AlertTitle } from '@/shared/ui/alert';
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
 */

type FilterStatus = Extract<WorkflowChangeStatus, 'pending' | 'applied' | 'rejected'>;

interface ChangesData {
  schemas: Record<string, WorkflowSchema>;
  wfNames: Record<string, string>;
  wfTypes: Record<string, string>;
  changes: WorkflowChangeRecord[];
}

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

async function loadChanges(status: FilterStatus, signal?: AbortSignal): Promise<ChangesData> {
  const workflows = await listWorkflows(signal);
  const schemas: Record<string, WorkflowSchema> = {};
  const wfNames: Record<string, string> = {};
  const wfTypes: Record<string, string> = {};
  const changes: WorkflowChangeRecord[] = [];
  await Promise.all(
    workflows.map(async (workflow) => {
      wfNames[workflow.id] = workflow.name;
      wfTypes[workflow.id] = workflow.type.toUpperCase();
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

export function AdminApprovalsPage(): ReactNode {
  const queryClient = useQueryClient();
  // Чек-лист онбординга §7.5: открытие согласований (no-op без провайдера).
  const { completeChecklistItem } = useOnboarding();
  useEffect(() => {
    completeChecklistItem('open-approvals');
  }, [completeChecklistItem]);
  const [statusFilter, setStatusFilter] = useState<FilterStatus>('pending');
  const [approveFor, setApproveFor] = useState<WorkflowChangeRecord | null>(null);
  const [rejectFor, setRejectFor] = useState<WorkflowChangeRecord | null>(null);
  const [rejectReason, setRejectReason] = useState('');

  const query = useQuery({
    queryKey: ['workflow-changes-admin', statusFilter],
    queryFn: ({ signal }) => loadChanges(statusFilter, signal),
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
    onSuccess: (record) => {
      setApproveFor(null);
      const migrated = record.migration_report?.migrated_requests ?? 0;
      toastSuccess(
        'Схема применена',
        migrated > 0
          ? `Перенесено заявок: ${migrated}. Все открытые доски обновятся автоматически.`
          : 'Перенос заявок не потребовался. Все открытые доски обновятся автоматически.',
      );
      invalidateAll();
    },
    onError: (error) => {
      toastError(error, { title: 'Не удалось применить пакет' });
      // 409: схема изменилась после создания пакета — показываем свежие данные
      invalidateAll();
    },
  });

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
            return (
              <motion.li
                key={change.id}
                variants={staggerItem}
                className="rounded-lg border bg-card p-4 shadow-card"
              >
                <div className="space-y-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="rounded-sm bg-primary-tint-2 px-2 py-0.5 text-xs font-semibold text-primary-active">
                      {data.wfTypes[change.workflow_id] ?? 'WF'}
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
                  <ImpactSummary impact={change.impact} />
                  {change.status === 'rejected' && change.decision_comment ? (
                    <Alert variant="warning">
                      <TriangleAlert aria-hidden="true" />
                      <AlertTitle className="text-balance">
                        Причина отклонения: {change.decision_comment}
                      </AlertTitle>
                    </Alert>
                  ) : null}
                  {change.status === 'applied' ? (
                    <p className="text-sm text-muted-foreground">
                      Применён {formatDateTime(change.applied_at ?? change.decided_at ?? null)}
                      {change.migration_report
                        ? ` · перенесено заявок: ${change.migration_report.migrated_requests}`
                        : ''}
                    </p>
                  ) : null}
                  {change.status === 'pending' ? (
                    <>
                      <Separator />
                      <div className="flex flex-wrap gap-2">
                        <Button onClick={() => setApproveFor(change)}>
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
                </div>
              </motion.li>
            );
          })}
        </motion.ul>
      );
    }
  }

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
          onOpenChange={(next) => (!next && !approveMutation.isPending ? setApproveFor(null) : undefined)}
        >
          <DialogContent className="max-h-[85vh] gap-4 overflow-y-auto sm:max-w-[640px]">
            <DialogHeader>
              <DialogTitle>Согласовать и опубликовать изменения?</DialogTitle>
            </DialogHeader>
            <Alert variant="warning">
              <TriangleAlert aria-hidden="true" />
              <AlertTitle>Схема применится сразу для всех — версий нет</AlertTitle>
              <p className="col-start-2 text-sm">
                Заявки из удаляемых этапов будут перенесены атомарно, в историю каждой добавится
                запись, ответственные получат уведомления.
              </p>
            </Alert>
            <OperationsDiff
              operations={approveFor.operations}
              schema={query.data?.schemas[approveFor.workflow_id]}
              impact={approveFor.impact}
            />
            <ImpactSummary impact={approveFor.impact} />
            <DialogFooter>
              <Button
                variant="outline"
                disabled={approveMutation.isPending}
                onClick={() => setApproveFor(null)}
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
                Применить схему
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
