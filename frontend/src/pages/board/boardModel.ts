import type {
  RequestItem,
  WorkflowSchema,
  WorkflowStatus,
  WorkflowTransition,
  WorkflowType,
} from '@/shared/api/types';
import type { PermissionCode } from '@/shared/auth/permissions';
import { dayjs } from '@/shared/lib/dayjs';

/**
 * Модель канбана (ux.md §7): колонки — этапы живой схемы workflow в порядке
 * `position`; допустимость переходов — строго по графу `transitions` c
 * фильтром `allowed_roles` (api-contract.md §4.3, workflow-engine.md §7).
 * Роли сравниваются с ролями пользователя, способными выполнять переходы
 * в процессе (`access.transition_roles` из /auth/me, F2).
 */

export interface BoardColumnModel {
  status: WorkflowStatus;
  requests: RequestItem[];
  totalAmount: number;
}

export function buildColumns(
  schema: WorkflowSchema,
  requests: RequestItem[],
): BoardColumnModel[] {
  const byStatus = new Map<string, RequestItem[]>();
  for (const request of requests) {
    const bucket = byStatus.get(request.status.id);
    if (bucket) bucket.push(request);
    else byStatus.set(request.status.id, [request]);
  }
  return [...schema.statuses]
    .sort((a, b) => a.position - b.position)
    .map((status) => {
      const items = byStatus.get(status.id) ?? [];
      return {
        status,
        requests: items,
        totalAmount: items.reduce((sum, r) => sum + (r.amount ? Number(r.amount) : 0), 0),
      };
    });
}

export interface TransitionTarget {
  transition: WorkflowTransition;
  toStatus: WorkflowStatus;
}

/**
 * Переход доступен ролям: пустой `allowed_roles` — всем, кто может выполнять
 * переходы в процессе (как на сервере), иначе — пересечение с ролями пользователя.
 */
export function transitionAllowedFor(
  transition: WorkflowTransition,
  roleCodes: readonly string[],
): boolean {
  if (transition.allowed_roles.length === 0) return roleCodes.length > 0;
  return transition.allowed_roles.some((role) => roleCodes.includes(role));
}

/** Рёбра графа из статуса, разрешённые ролям пользователя (вкл. возвраты). */
export function transitionsFrom(
  schema: WorkflowSchema,
  fromStatusId: string,
  roleCodes: readonly string[],
): TransitionTarget[] {
  const statusById = new Map(schema.statuses.map((s) => [s.id, s]));
  return schema.transitions
    .filter(
      (transition) =>
        transition.from_status_id === fromStatusId && transitionAllowedFor(transition, roleCodes),
    )
    .flatMap((transition) => {
      const toStatus = statusById.get(transition.to_status_id);
      return toStatus ? [{ transition, toStatus }] : [];
    });
}

/** Человекочитаемый список разрешённых целей — для toast'а об отбитом дропе. */
export function allowedTargetsLabel(targets: TransitionTarget[]): string {
  if (targets.length === 0) return 'разрешённых переходов нет';
  return targets.map((t) => `«${t.toStatus.name}»`).join(', ');
}

/** N дней на этапе (ux.md §7.1) — от status_updated_at (deal.stage_entered_at). */
export function daysOnStage(request: RequestItem): number {
  return Math.max(0, dayjs().diff(dayjs(request.status_updated_at), 'day'));
}

/** Проверка права в процессе — `hasPermission` из AuthContext. */
export type CanInWorkflow = (code: PermissionCode, workflowType: WorkflowType) => boolean;

/**
 * Может ли пользователь перетаскивать карточку (ux.md §7): право переводить
 * заявки в процессе и либо своя заявка, либо право на чужие (как `can_act_on_deal`).
 */
export function canDragRequest(
  request: RequestItem,
  can: CanInWorkflow,
  userId: string | undefined,
): boolean {
  const wf = request.workflow_type;
  if (!can('requests:transition', wf)) return false;
  return can('requests:write_any', wf) || (Boolean(userId) && request.assignee?.id === userId);
}
