import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { UiPosition, WorkflowSchema } from '@/shared/api/types';
import {
  createDraftFromSchema,
  newLocalId,
  slugifyStageName,
  type DraftStage,
  type DraftTransition,
  type StageDeleteMeta,
  type WorkflowDraft,
} from './model';

/**
 * Клиентский черновик конструктора (ux.md §10.4): zustand + localStorage,
 * F5 черновик не теряет, на сервер до отправки пакета ничего не пишется.
 * Черновики хранятся по workflow_id — B2B и B2C правятся независимо.
 */

interface WorkflowDraftStore {
  drafts: Record<string, WorkflowDraft>;
  createDraft: (schema: WorkflowSchema) => void;
  discardDraft: (workflowId: string) => void;
  /** Точечная правка полей этапа (панель свойств). */
  patchStage: (workflowId: string, stageId: string, patch: Partial<DraftStage>) => void;
  moveStage: (workflowId: string, stageId: string, position: UiPosition) => void;
  /** Новый этап; код генерируется транслитом из названия. Возвращает id. */
  addStage: (workflowId: string, at: UiPosition) => string;
  /** Удаление нового (ещё не существующего на сервере) этапа — просто из черновика. */
  removeNewStage: (workflowId: string, stageId: string) => void;
  /** Пометить существующий этап на удаление (red-сценарий §10.5). */
  markStageDeleted: (workflowId: string, stageId: string, meta: StageDeleteMeta) => void;
  restoreStage: (workflowId: string, stageId: string) => void;
  /** Переименование существующего этапа с посимвольным подтверждением. */
  renameStage: (workflowId: string, stageId: string, newName: string, confirmName: string) => void;
  addTransition: (
    workflowId: string,
    fromStatusId: string,
    toStatusId: string,
  ) => string | null;
  patchTransition: (
    workflowId: string,
    transitionId: string,
    patch: Partial<DraftTransition>,
  ) => void;
  removeTransition: (workflowId: string, transitionId: string) => void;
}

function withDraft(
  drafts: Record<string, WorkflowDraft>,
  workflowId: string,
  mutate: (draft: WorkflowDraft) => WorkflowDraft,
): Record<string, WorkflowDraft> {
  const draft = drafts[workflowId];
  if (!draft) return drafts;
  return { ...drafts, [workflowId]: mutate(draft) };
}

export const useWorkflowDraftStore = create<WorkflowDraftStore>()(
  persist(
    (set, get) => ({
      drafts: {},

      createDraft: (schema) =>
        set((state) => ({
          drafts: { ...state.drafts, [schema.id]: createDraftFromSchema(schema) },
        })),

      discardDraft: (workflowId) =>
        set((state) => {
          const { [workflowId]: _removed, ...rest } = state.drafts;
          return { drafts: rest };
        }),

      patchStage: (workflowId, stageId, patch) =>
        set((state) => ({
          drafts: withDraft(state.drafts, workflowId, (draft) => ({
            ...draft,
            stages: draft.stages.map((s) => (s.id === stageId ? { ...s, ...patch } : s)),
          })),
        })),

      moveStage: (workflowId, stageId, position) =>
        get().patchStage(workflowId, stageId, { ui_position: position }),

      addStage: (workflowId, at) => {
        const id = newLocalId();
        set((state) => ({
          drafts: withDraft(state.drafts, workflowId, (draft) => {
            const n = draft.stages.length + 1;
            const name = `Новый этап ${n}`;
            const stage: DraftStage = {
              id,
              code: slugifyStageName(name, draft.stages.map((s) => s.code)),
              name,
              color: '#1677ff',
              is_initial: false,
              is_terminal: false,
              terminal_outcome: null,
              stuck_threshold_days: null,
              triggers_lms_handover: false,
              position: Math.max(0, ...draft.stages.map((s) => s.position)) + 1,
              ui_position: at,
            };
            return { ...draft, stages: [...draft.stages, stage] };
          }),
        }));
        return id;
      },

      removeNewStage: (workflowId, stageId) =>
        set((state) => ({
          drafts: withDraft(state.drafts, workflowId, (draft) => ({
            ...draft,
            stages: draft.stages.filter((s) => s.id !== stageId),
            transitions: draft.transitions.filter(
              (t) => t.from_status_id !== stageId && t.to_status_id !== stageId,
            ),
          })),
        })),

      markStageDeleted: (workflowId, stageId, meta) =>
        set((state) => ({
          drafts: withDraft(state.drafts, workflowId, (draft) => ({
            ...draft,
            deleted: { ...draft.deleted, [stageId]: meta },
          })),
        })),

      restoreStage: (workflowId, stageId) =>
        set((state) => ({
          drafts: withDraft(state.drafts, workflowId, (draft) => {
            const { [stageId]: _removed, ...rest } = draft.deleted;
            return { ...draft, deleted: rest };
          }),
        })),

      renameStage: (workflowId, stageId, newName, confirmName) =>
        set((state) => ({
          drafts: withDraft(state.drafts, workflowId, (draft) => ({
            ...draft,
            stages: draft.stages.map((s) =>
              s.id === stageId ? { ...s, name: newName } : s,
            ),
            renameConfirms: { ...draft.renameConfirms, [stageId]: confirmName },
          })),
        })),

      addTransition: (workflowId, fromStatusId, toStatusId) => {
        const draft = get().drafts[workflowId];
        if (!draft || fromStatusId === toStatusId) return null;
        const duplicate = draft.transitions.some(
          (t) => t.from_status_id === fromStatusId && t.to_status_id === toStatusId,
        );
        if (duplicate) return null;
        const from = draft.stages.find((s) => s.id === fromStatusId);
        const to = draft.stages.find((s) => s.id === toStatusId);
        if (!from || !to || from.is_terminal) return null;
        const id = newLocalId();
        // возврат «назад» угадываем по порядку колонок; пользователь может поменять kind
        const isReturn = to.position < from.position && !to.is_terminal;
        const transition: DraftTransition = {
          id,
          from_status_id: fromStatusId,
          to_status_id: toStatusId,
          kind: isReturn ? 'return' : 'forward',
          name: isReturn ? `Вернуть на «${to.name}»` : `В «${to.name}»`,
          requires_comment: isReturn,
          allowed_roles: ['kam', 'head_kam', 'admin'],
        };
        set((state) => ({
          drafts: withDraft(state.drafts, workflowId, (d) => ({
            ...d,
            transitions: [...d.transitions, transition],
          })),
        }));
        return id;
      },

      patchTransition: (workflowId, transitionId, patch) =>
        set((state) => ({
          drafts: withDraft(state.drafts, workflowId, (draft) => ({
            ...draft,
            transitions: draft.transitions.map((t) => {
              if (t.id !== transitionId) return t;
              const next = { ...t, ...patch };
              // возврат всегда требует комментарий (CHECK в БД, engine §3.4)
              if (next.kind === 'return') next.requires_comment = true;
              return next;
            }),
          })),
        })),

      removeTransition: (workflowId, transitionId) =>
        set((state) => ({
          drafts: withDraft(state.drafts, workflowId, (draft) => ({
            ...draft,
            transitions: draft.transitions.filter((t) => t.id !== transitionId),
          })),
        })),
    }),
    {
      name: 'crm.workflow.drafts',
      version: 1,
    },
  ),
);
