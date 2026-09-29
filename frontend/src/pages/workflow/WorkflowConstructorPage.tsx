import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  MiniMap,
  Panel,
  ReactFlow,
  ReactFlowProvider,
  applyEdgeChanges,
  applyNodeChanges,
  useReactFlow,
  type Connection,
  type EdgeChange,
  type NodeChange,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Info, Network, Plus, Send, TriangleAlert, X } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { listRequests } from '@/shared/api/endpoints/requests';
import {
  createWorkflowChange,
  impactPreview,
  listWorkflowChanges,
} from '@/shared/api/endpoints/workflowChanges';
import { useOnboarding } from '@/features/onboarding';
import { getWorkflow, listWorkflows } from '@/shared/api/endpoints/workflows';
import type {
  WorkflowChangeImpact,
  WorkflowOperation,
  WorkflowType,
} from '@/shared/api/types';
import { useAuth } from '@/shared/auth/AuthContext';
import { STATUS_TRIPLES, SURFACE } from '@/shared/config/tokens';
import { cn } from '@/shared/lib/cn';
import { toastError, toastSuccess } from '@/shared/lib/toast';
import { useSseInvalidate } from '@/shared/lib/useSseInvalidate';
import {
  LocalErrorState,
  LocalLoadingState,
  LocalPageHeader,
  LocalSegmented,
} from '@/components/wp5/PageChrome';
import { Alert, AlertDescription, AlertTitle } from '@/shared/ui/alert';
import { Button } from '@/shared/ui/button';
import { ConfirmDialog } from '@/shared/ui/confirm-dialog';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/ui/tooltip';
import { useWorkflowDraftStore } from './draftStore';
import {
  CANVAS_GRID,
  aliveStages,
  aliveTransitions,
  autoLayout,
  buildOperations,
  hasDangerousOps,
  layoutForSchema,
  migrateOptionsFor,
  validateDraft,
  type DraftStage,
} from './model';
import { PropertiesPanel, type CanvasSelection } from './PropertiesPanel';
import { StageNode, type StageFlowNode } from './StageNode';
import { DeleteStageModal, RenameStageModal } from './StageModals';
import { SubmitChangesModal } from './SubmitChangesModal';
import { TransitionEdge, type TransitionFlowEdge } from './TransitionEdge';

/**
 * Конструктор workflow (redesign.md §6.6): канва React Flow с живой схемой,
 * узлы-карточки со статусным цветом и анимированные рёбра под токенами §1,
 * клиентский черновик (zustand + localStorage), отправка изменений пакетом
 * операций с impact-превью; опасные операции — ConfirmDialog с посимвольным
 * подтверждением и согласованием администратора.
 */

const REQUESTS_LIMIT = 200;

const nodeTypes = { stage: StageNode };
const edgeTypes = { transition: TransitionEdge };

/**
 * Бегущий пунктир выбранного ребра (§6.6 «анимированные рёбра»): анимация
 * сообщает состояние выбора; при prefers-reduced-motion отключается.
 */
const EDGE_ANIMATION_CSS = `
@keyframes wf-dash { to { stroke-dashoffset: -20; } }
.wf-edge-selected { animation: wf-dash 1.2s linear infinite; }
@media (prefers-reduced-motion: reduce) { .wf-edge-selected { animation: none; } }
`;

interface SubmitState {
  operations: WorkflowOperation[];
  impact: WorkflowChangeImpact | null;
  impactUnavailable: boolean;
}

/** Бейдж состояния схемы (Опубликована / Черновик / На согласовании). */
function SchemaStatusBadge({
  tone,
  children,
}: {
  tone: 'success' | 'warning' | 'progress';
  children: ReactNode;
}): ReactNode {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-sm px-2 py-0.5 text-xs font-medium',
        tone === 'success' && 'bg-status-success-tint text-status-success-deep',
        tone === 'warning' && 'bg-status-warning-tint text-status-warning-deep',
        tone === 'progress' && 'bg-status-progress-tint text-status-progress-deep',
      )}
    >
      <span
        className={cn(
          'size-1.5 rounded-full',
          tone === 'success' && 'bg-status-success',
          tone === 'warning' && 'bg-status-warning',
          tone === 'progress' && 'bg-status-progress',
        )}
        aria-hidden="true"
      />
      {children}
    </span>
  );
}

function ConstructorInner(): ReactNode {
  const queryClient = useQueryClient();
  const { hasRole } = useAuth();
  // Чек-лист онбординга §7.5: no-op без провайдера.
  const { completeChecklistItem } = useOnboarding();
  const reactFlow = useReactFlow();
  const [searchParams, setSearchParams] = useSearchParams();

  const wfType: WorkflowType = searchParams.get('wf') === 'b2c' ? 'b2c' : 'b2b';
  const canEdit = hasRole('admin', 'head_kam');
  const isAdmin = hasRole('admin');

  // --- Данные ---------------------------------------------------------------
  const workflowsQuery = useQuery({
    queryKey: ['workflows'],
    queryFn: ({ signal }) => listWorkflows(signal),
    staleTime: 60_000,
  });
  const workflowId = workflowsQuery.data?.find((w) => w.type === wfType)?.id ?? '';

  const schemaQuery = useQuery({
    queryKey: ['workflow-schema', workflowId],
    queryFn: ({ signal }) => getWorkflow(workflowId, signal),
    enabled: Boolean(workflowId),
    staleTime: 60_000,
  });
  const schema = schemaQuery.data;

  const requestsQuery = useQuery({
    queryKey: ['wf-requests-counts', wfType],
    queryFn: ({ signal }) =>
      listRequests({ workflow_type: wfType }, { limit: REQUESTS_LIMIT, signal }),
    staleTime: 30_000,
  });
  const counts = useMemo(() => {
    const map = new Map<string, number>();
    for (const request of requestsQuery.data?.items ?? []) {
      map.set(request.status.id, (map.get(request.status.id) ?? 0) + 1);
    }
    return map;
  }, [requestsQuery.data]);
  const countsApprox = (requestsQuery.data?.total ?? 0) > REQUESTS_LIMIT;
  const countsReady = requestsQuery.isSuccess;

  const pendingQuery = useQuery({
    queryKey: ['workflow-changes', workflowId, 'pending'],
    queryFn: ({ signal }) => listWorkflowChanges(workflowId, 'pending', signal),
    enabled: Boolean(workflowId) && canEdit,
    staleTime: 30_000,
    retry: 0,
  });
  const pendingChange = pendingQuery.data?.[0];

  // --- Черновик ---------------------------------------------------------------
  const draft = useWorkflowDraftStore((s) => (workflowId ? s.drafts[workflowId] : undefined));
  const store = useWorkflowDraftStore();
  const editing = Boolean(draft) && canEdit;

  const operations = useMemo(
    () => (schema && draft ? buildOperations(schema, draft) : []),
    [schema, draft],
  );
  const issues = useMemo(() => (draft ? validateDraft(draft) : []), [draft]);
  const dangerous = hasDangerousOps(operations);

  // --- Realtime ---------------------------------------------------------------
  const [liveChangedBanner, setLiveChangedBanner] = useState(false);
  const sseKeys = useMemo(
    () => [['workflow-schema'], ['workflows'], ['workflow-changes'], ['wf-requests-counts']],
    [],
  );
  useSseInvalidate('workflow.changed', sseKeys, () => {
    setLiveChangedBanner(true);
    return true;
  });

  // --- Канва: узлы и рёбра ----------------------------------------------------
  const [nodes, setNodes] = useState<StageFlowNode[]>([]);
  const [edges, setEdges] = useState<TransitionFlowEdge[]>([]);
  const [selection, setSelection] = useState<CanvasSelection | null>(null);

  const issueStageIds = useMemo(
    () => new Set(issues.flatMap((i) => (i.stageId ? [i.stageId] : []))),
    [issues],
  );

  useEffect(() => {
    if (!schema) {
      setNodes([]);
      setEdges([]);
      return;
    }
    const layout = draft ? null : layoutForSchema(schema);
    const stages: DraftStage[] = draft
      ? draft.stages
      : schema.statuses.map((s) => ({ ...s, ui_position: layout?.[s.id] ?? null }));
    const stageNameById = new Map(stages.map((s) => [s.id, s.name]));
    // выделение НЕ пересчитываем из состояния — сохраняем флаги React Flow,
    // иначе перестройка узлов гоняется с событиями selection (потеря выбора)
    setNodes((prev) => {
      const selectedIds = new Set(prev.filter((n) => n.selected).map((n) => n.id));
      return stages.map((stage) => {
        const ghost = Boolean(draft?.deleted[stage.id]);
        return {
          id: stage.id,
          type: 'stage' as const,
          position: stage.ui_position ?? { x: 40, y: 40 },
          draggable: editing,
          selected: selectedIds.has(stage.id),
          data: {
            stage,
            count: countsReady ? counts.get(stage.id) ?? 0 : null,
            approxCount: countsApprox,
            ghost,
            migrateToName: ghost
              ? stageNameById.get(draft?.deleted[stage.id]?.migrate_to_status_id ?? '')
              : undefined,
            hasIssue: editing && issueStageIds.has(stage.id),
            editing,
          },
        };
      });
    });
    const transitions = draft ? draft.transitions : schema.transitions;
    setEdges((prev) => {
      const selectedIds = new Set(prev.filter((e) => e.selected).map((e) => e.id));
      return transitions.map((transition) => {
        const ghost = Boolean(
          draft?.deleted[transition.from_status_id] || draft?.deleted[transition.to_status_id],
        );
        return {
          id: transition.id,
          type: 'transition' as const,
          source: transition.from_status_id,
          target: transition.to_status_id,
          selected: selectedIds.has(transition.id),
          style: ghost ? { opacity: 0.3 } : undefined,
          markerEnd: {
            type: MarkerType.ArrowClosed,
            color: transition.kind === 'return' ? STATUS_TRIPLES.danger.core : SURFACE.primary,
          },
          data: { transition },
        };
      });
    });
  }, [schema, draft, editing, counts, countsApprox, countsReady, issueStageIds]);

  const onNodesChange = useCallback(
    (changes: NodeChange<StageFlowNode>[]) =>
      setNodes((nds) => applyNodeChanges(changes, nds)),
    [],
  );
  const onEdgesChange = useCallback(
    (changes: EdgeChange<TransitionFlowEdge>[]) =>
      setEdges((eds) => applyEdgeChanges(changes, eds)),
    [],
  );

  /** Сброс выбора и в состоянии панели, и во флагах узлов React Flow. */
  const clearCanvasSelection = useCallback(() => {
    setSelection(null);
    setNodes((nds) => nds.map((n) => (n.selected ? { ...n, selected: false } : n)));
    setEdges((eds) => eds.map((e) => (e.selected ? { ...e, selected: false } : e)));
  }, []);

  const focusStage = useCallback(
    (stageId: string) => {
      setSelection({ kind: 'stage', id: stageId });
      setNodes((nds) => nds.map((n) => ({ ...n, selected: n.id === stageId })));
      setEdges((eds) => eds.map((e) => (e.selected ? { ...e, selected: false } : e)));
      void reactFlow.fitView({ nodes: [{ id: stageId }], duration: 300, maxZoom: 1.1 });
    },
    [reactFlow],
  );

  const handleConnect = useCallback(
    (connection: Connection) => {
      if (!editing || !workflowId || !draft) return;
      const { source, target } = connection;
      if (!source || !target) return;
      if (source === target) {
        toastError(new Error('Переход этапа в самого себя запрещён'), {
          title: 'Нельзя добавить переход',
        });
        return;
      }
      if (
        draft.transitions.some((t) => t.from_status_id === source && t.to_status_id === target)
      ) {
        toastError(new Error('Такой переход уже есть — отредактируйте существующий'), {
          title: 'Нельзя добавить переход',
        });
        return;
      }
      const id = store.addTransition(workflowId, source, target);
      if (id) {
        setSelection({ kind: 'transition', id });
        // ребро появится в edges после перестройки — подсветим следующим кадром
        requestAnimationFrame(() =>
          setEdges((eds) => eds.map((e) => ({ ...e, selected: e.id === id }))),
        );
      }
    },
    [editing, workflowId, draft, store],
  );

  const handleAddStage = useCallback(() => {
    if (!workflowId || !draft) return;
    const maxX = Math.max(0, ...draft.stages.map((s) => s.ui_position?.x ?? 0));
    const id = store.addStage(workflowId, { x: maxX + 280, y: 80 });
    setSelection({ kind: 'stage', id });
    requestAnimationFrame(() =>
      setNodes((nds) => nds.map((n) => ({ ...n, selected: n.id === id }))),
    );
    setTimeout(() => {
      void reactFlow.fitView({ nodes: [{ id }], duration: 300, maxZoom: 1 });
    }, 50);
  }, [workflowId, draft, store, reactFlow]);

  const handleAutoLayout = useCallback(() => {
    if (!workflowId || !draft) return;
    const layout = autoLayout(aliveStages(draft), aliveTransitions(draft));
    for (const [stageId, position] of Object.entries(layout)) {
      store.moveStage(workflowId, stageId, position);
    }
    setTimeout(() => void reactFlow.fitView({ duration: 300 }), 50);
  }, [workflowId, draft, store, reactFlow]);

  // --- Модалки опасных операций -----------------------------------------------
  const [renameFor, setRenameFor] = useState<string | null>(null);
  const [deleteFor, setDeleteFor] = useState<string | null>(null);
  const [submitState, setSubmitState] = useState<SubmitState | null>(null);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [confirmRecreate, setConfirmRecreate] = useState(false);

  const renameBase = renameFor ? schema?.statuses.find((s) => s.id === renameFor) : undefined;
  const renameDraft = renameFor ? draft?.stages.find((s) => s.id === renameFor) : undefined;
  const deleteBase = deleteFor ? schema?.statuses.find((s) => s.id === deleteFor) : undefined;
  const deleteMigrate =
    deleteFor && schema && draft
      ? migrateOptionsFor(schema, draft, deleteFor)
      : { options: [], defaultId: null };
  const deleteEdgeCounts = useMemo(() => {
    if (!deleteFor || !draft) return { incoming: 0, outgoing: 0 };
    const alive = aliveTransitions(draft);
    return {
      incoming: alive.filter((t) => t.to_status_id === deleteFor).length,
      outgoing: alive.filter((t) => t.from_status_id === deleteFor).length,
    };
  }, [deleteFor, draft]);

  // --- Отправка пакета ----------------------------------------------------------
  const previewMutation = useMutation({
    mutationFn: (ops: WorkflowOperation[]) => impactPreview(workflowId, ops),
    meta: { silent: true },
    onSuccess: (impact, ops) =>
      setSubmitState({ operations: ops, impact, impactUnavailable: false }),
    onError: (_error, ops) =>
      setSubmitState({ operations: ops, impact: null, impactUnavailable: true }),
  });

  const submitMutation = useMutation({
    mutationFn: ({ comment, ops }: { comment: string; ops: WorkflowOperation[] }) =>
      createWorkflowChange(workflowId, { comment: comment || undefined, operations: ops }),
    meta: { silent: true },
    onSuccess: (record) => {
      store.discardDraft(workflowId);
      setSubmitState(null);
      clearCanvasSelection();
      if (record.status === 'applied') {
        toastSuccess('Изменения применены', 'Новая схема действует для всех заявок');
      } else {
        toastSuccess(
          'Пакет отправлен на согласование',
          'Администратор увидит изменения в разделе «Согласования»',
        );
      }
      void queryClient.invalidateQueries({ queryKey: ['workflow-schema'] });
      void queryClient.invalidateQueries({ queryKey: ['workflows'] });
      void queryClient.invalidateQueries({ queryKey: ['workflow-changes'] });
      void queryClient.invalidateQueries({ queryKey: ['board-requests'] });
      completeChecklistItem('wf-submit');
    },
    onError: (error) => {
      toastError(error, { title: 'Не удалось отправить изменения схемы' });
      // 409/400: схема могла измениться — перечитываем живой граф
      void queryClient.invalidateQueries({ queryKey: ['workflow-schema'] });
    },
  });

  const submitDisabledReason = !draft
    ? 'Нет черновика'
    : operations.length === 0
      ? 'В черновике нет изменений'
      : issues.length > 0
        ? 'Исправьте ошибки схемы (список — на панели справа)'
        : pendingChange
          ? 'Уже есть пакет на согласовании — дождитесь решения администратора'
          : null;

  // --- Рендер --------------------------------------------------------------------
  if (workflowsQuery.isError) {
    return (
      <LocalErrorState
        error={workflowsQuery.error}
        title="Не удалось загрузить список процессов"
        onRetry={() => void workflowsQuery.refetch()}
      />
    );
  }
  if (schemaQuery.isError) {
    return (
      <LocalErrorState
        error={schemaQuery.error}
        title="Не удалось загрузить схему процесса"
        onRetry={() => void schemaQuery.refetch()}
      />
    );
  }
  if (!schema) return <LocalLoadingState rows={10} />;

  const statusBadge = pendingChange ? (
    <SchemaStatusBadge tone="progress">На согласовании</SchemaStatusBadge>
  ) : editing ? (
    <SchemaStatusBadge tone="warning">Черновик (ваш)</SchemaStatusBadge>
  ) : (
    <SchemaStatusBadge tone="success">Опубликована</SchemaStatusBadge>
  );

  const submitButton = (
    <Button
      data-tour="wf-submit"
      disabled={Boolean(submitDisabledReason) || previewMutation.isPending}
      onClick={() => previewMutation.mutate(operations)}
    >
      <Send aria-hidden="true" />
      {isAdmin && !dangerous ? 'Опубликовать изменения' : 'Отправить на согласование'}
    </Button>
  );

  return (
    <>
      <style>{EDGE_ANIMATION_CSS}</style>
      <LocalPageHeader
        title="Конструктор процессов"
        subtitle="Одна живая схема на процесс, без версий: изменения применяются сразу для всех (опасные — после согласования администратора)"
        extra={
          canEdit ? (
            editing ? (
              <>
                <Button variant="outline" onClick={() => setConfirmDiscard(true)}>
                  Отменить черновик
                </Button>
                {submitDisabledReason ? (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className="inline-flex">{submitButton}</span>
                    </TooltipTrigger>
                    <TooltipContent>{submitDisabledReason}</TooltipContent>
                  </Tooltip>
                ) : (
                  submitButton
                )}
              </>
            ) : (
              <Button
                disabled={!workflowId}
                onClick={() => {
                  store.createDraft(schema);
                  clearCanvasSelection();
                  completeChecklistItem('wf-draft');
                }}
              >
                Создать черновик
              </Button>
            )
          ) : undefined
        }
      />

      {liveChangedBanner ? (
        <Alert variant="info" className="mb-3">
          <Info aria-hidden="true" />
          <AlertTitle className="flex items-start justify-between gap-2">
            Схема обновлена администратором — канва перечитана
            <button
              type="button"
              aria-label="Закрыть уведомление"
              className="shrink-0 rounded-sm p-0.5 hover:bg-status-progress-tint"
              onClick={() => setLiveChangedBanner(false)}
            >
              <X className="size-4" aria-hidden="true" />
            </button>
          </AlertTitle>
        </Alert>
      ) : null}
      {editing && draft && draft.baseUpdatedAt !== schema.updated_at ? (
        <Alert variant="warning" className="mb-3">
          <TriangleAlert aria-hidden="true" />
          <AlertTitle>Живая схема изменилась после создания черновика</AlertTitle>
          <AlertDescription>
            <p>
              Пакет будет рассчитан относительно актуальной схемы; конфликтующие операции сервер
              отклонит при применении.
            </p>
            <Button
              variant="outline"
              size="sm"
              className="mt-2"
              onClick={() => setConfirmRecreate(true)}
            >
              Пересоздать черновик
            </Button>
          </AlertDescription>
        </Alert>
      ) : null}
      {pendingChange ? (
        <Alert variant="warning" className="mb-3">
          <TriangleAlert aria-hidden="true" />
          <AlertTitle className="text-balance">
            Пакет изменений {pendingChange.author ? `от ${pendingChange.author.full_name} ` : ''}
            ждёт согласования — отправка нового заблокирована до решения
          </AlertTitle>
          {isAdmin ? (
            <AlertDescription>
              <Button asChild size="sm" className="mt-2">
                <Link to="/admin/approvals">К согласованию</Link>
              </Button>
            </AlertDescription>
          ) : null}
        </Alert>
      ) : null}

      <div className="mb-3 flex flex-wrap items-center gap-3 rounded-lg border bg-card p-3 shadow-card">
        <LocalSegmented<WorkflowType>
          aria-label="Процесс"
          value={wfType}
          onChange={(value) =>
            setSearchParams((prev) => {
              const next = new URLSearchParams(prev);
              next.set('wf', value);
              return next;
            })
          }
          options={[
            { value: 'b2b', label: 'B2B (вузы)' },
            { value: 'b2c', label: 'B2C (физлица и юрлица)' },
          ]}
        />
        {statusBadge}
        {!canEdit ? (
          <span className="text-sm text-muted-foreground">
            Режим просмотра: редактирование доступно ролям admin и head_kam
          </span>
        ) : null}
      </div>

      <div className="flex items-stretch gap-3">
        <div
          data-tour="wf-canvas"
          className={cn(
            'relative min-w-0 flex-1 overflow-hidden rounded-lg bg-card',
            editing ? 'border-2 border-status-warning' : 'border border-border',
          )}
          style={{ height: 'calc(100vh - 280px)', minHeight: 480 }}
        >
          {editing ? (
            <div className="pointer-events-none absolute left-1/2 top-2 z-[5] -translate-x-1/2">
              <span className="rounded-sm bg-status-warning-tint px-2.5 py-1 text-xs font-semibold uppercase tracking-[0.25em] text-status-warning-deep">
                Черновик
              </span>
            </div>
          ) : null}
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={handleConnect}
            onNodeDragStop={(_event, node) =>
              editing && store.moveStage(workflowId, node.id, node.position)
            }
            onSelectionChange={({ nodes: selectedNodes, edges: selectedEdges }) => {
              const next: CanvasSelection | null =
                selectedNodes.length > 0
                  ? { kind: 'stage', id: selectedNodes[0].id }
                  : selectedEdges.length > 0
                    ? { kind: 'transition', id: selectedEdges[0].id }
                    : null;
              // стабильная ссылка при неизменном выборе — иначе цикл rebuild узлов
              setSelection((prev) =>
                prev?.kind === next?.kind && prev?.id === next?.id ? prev : next,
              );
            }}
            nodesDraggable={editing}
            nodesConnectable={editing}
            deleteKeyCode={null}
            snapToGrid
            snapGrid={[CANVAS_GRID, CANVAS_GRID]}
            fitView
            minZoom={0.3}
            maxZoom={1.6}
            proOptions={{ hideAttribution: true }}
          >
            <Background
              variant={BackgroundVariant.Dots}
              gap={20}
              size={1.5}
              color={SURFACE.border}
            />
            <Controls showInteractive={false} />
            <MiniMap
              pannable
              zoomable
              nodeColor={(node) =>
                (node.data as { stage?: DraftStage }).stage?.color ?? SURFACE.border
              }
              style={{ width: 160, height: 100 }}
            />
            {editing ? (
              <Panel position="top-left">
                <div className="flex flex-col gap-1.5">
                  <Button variant="outline" size="sm" onClick={handleAddStage}>
                    <Plus aria-hidden="true" /> Этап
                  </Button>
                  <Button variant="outline" size="sm" onClick={handleAutoLayout}>
                    <Network aria-hidden="true" /> Автораскладка
                  </Button>
                </div>
              </Panel>
            ) : null}
          </ReactFlow>
        </div>

        <PropertiesPanel
          editing={editing}
          schema={schema}
          draft={draft ?? null}
          selection={selection}
          issues={issues}
          opsCount={operations.length}
          counts={counts}
          countsApprox={countsApprox}
          countsReady={countsReady}
          onFocusStage={focusStage}
          onPatchStage={(stageId, patch) => store.patchStage(workflowId, stageId, patch)}
          onRequestRename={(stageId) => setRenameFor(stageId)}
          onResetRename={(stageId) => {
            const base = schema.statuses.find((s) => s.id === stageId);
            if (base) store.patchStage(workflowId, stageId, { name: base.name });
          }}
          onRequestDelete={(stageId) => setDeleteFor(stageId)}
          onRemoveNewStage={(stageId) => {
            store.removeNewStage(workflowId, stageId);
            clearCanvasSelection();
          }}
          onRestoreStage={(stageId) => store.restoreStage(workflowId, stageId)}
          onPatchTransition={(transitionId, patch) =>
            store.patchTransition(workflowId, transitionId, patch)
          }
          onRemoveTransition={(transitionId) => {
            store.removeTransition(workflowId, transitionId);
            clearCanvasSelection();
          }}
        />
      </div>

      <ConfirmDialog
        open={confirmDiscard}
        onOpenChange={setConfirmDiscard}
        tone="danger"
        title="Отменить черновик?"
        facts={[{ label: 'все несохранённые правки схемы будут потеряны' }]}
        confirmLabel="Отменить черновик"
        onConfirm={() => {
          store.discardDraft(workflowId);
          clearCanvasSelection();
          setConfirmDiscard(false);
        }}
      />
      <ConfirmDialog
        open={confirmRecreate}
        onOpenChange={setConfirmRecreate}
        tone="danger"
        title="Пересоздать черновик от актуальной схемы?"
        facts={[{ label: 'текущие правки черновика будут потеряны' }]}
        confirmLabel="Пересоздать"
        onConfirm={() => {
          store.createDraft(schema);
          clearCanvasSelection();
          setConfirmRecreate(false);
        }}
      />

      {renameBase && renameDraft ? (
        <RenameStageModal
          open
          baseName={renameBase.name}
          draftName={renameDraft.name}
          onConfirm={(newName, confirmName) => {
            store.renameStage(workflowId, renameBase.id, newName, confirmName);
            setRenameFor(null);
          }}
          onCancel={() => setRenameFor(null)}
        />
      ) : null}

      {deleteBase ? (
        <DeleteStageModal
          open
          baseName={deleteBase.name}
          requestsCount={countsReady ? counts.get(deleteBase.id) ?? 0 : null}
          approxCount={countsApprox}
          incomingCount={deleteEdgeCounts.incoming}
          outgoingCount={deleteEdgeCounts.outgoing}
          options={deleteMigrate.options}
          defaultId={deleteMigrate.defaultId}
          onConfirm={(migrateToStatusId, confirmName) => {
            store.markStageDeleted(workflowId, deleteBase.id, {
              confirm_name: confirmName,
              migrate_to_status_id: migrateToStatusId,
            });
            setDeleteFor(null);
            clearCanvasSelection();
          }}
          onCancel={() => setDeleteFor(null)}
        />
      ) : null}

      {submitState ? (
        <SubmitChangesModal
          open
          operations={submitState.operations}
          schema={schema}
          impact={submitState.impact}
          impactUnavailable={submitState.impactUnavailable}
          dangerous={hasDangerousOps(submitState.operations)}
          isAdmin={isAdmin}
          submitting={submitMutation.isPending}
          onSubmit={(comment) =>
            submitMutation.mutate({ comment, ops: submitState.operations })
          }
          onCancel={() => setSubmitState(null)}
        />
      ) : null}
    </>
  );
}

/** Обёртка ReactFlowProvider — внутри доступен useReactFlow (fitView и пр.). */
export default function WorkflowConstructorPage(): ReactNode {
  return (
    <ReactFlowProvider>
      <ConstructorInner />
    </ReactFlowProvider>
  );
}
