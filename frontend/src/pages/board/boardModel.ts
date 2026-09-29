import type {
  RequestItem,
  WorkflowSchema,
  WorkflowStatus,
  WorkflowTransition,
} from '@/shared/api/types';
import type { AppRole } from '@/shared/auth/roles';
import { dayjs } from '@/shared/lib/dayjs';

/**
 * Модель канбана (ux.md §7): колонки — этапы живой схемы workflow в порядке
 * `position`; допустимость переходов — строго по графу `transitions` c
 * фильтром `allowed_roles` (api-contract.md §4.3, workflow-engine.md §7).
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

/** Рёбра графа из статуса, разрешённые ролям пользователя (вкл. возвраты). */
export function transitionsFrom(
  schema: WorkflowSchema,
  fromStatusId: string,
  roles: readonly AppRole[],
): TransitionTarget[] {
  const statusById = new Map(schema.statuses.map((s) => [s.id, s]));
  return schema.transitions
    .filter(
      (transition) =>
        transition.from_status_id === fromStatusId &&
        transition.allowed_roles.some((role) => (roles as readonly string[]).includes(role)),
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

/** Может ли пользователь перетаскивать конкретную карточку (ux.md §7). */
export function canDragRequest(
  request: RequestItem,
  roles: readonly AppRole[],
  userId: string | undefined,
): boolean {
  if (roles.includes('admin') || roles.includes('head_kam')) return true;
  if (roles.includes('kam')) return request.assignee?.id === userId;
  return false;
}
