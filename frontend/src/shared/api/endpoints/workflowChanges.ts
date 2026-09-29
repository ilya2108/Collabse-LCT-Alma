import { api } from '../client';
import type {
  WorkflowChangeImpact,
  WorkflowChangeRecord,
  WorkflowChangeStatus,
  WorkflowOperation,
} from '../types';

/**
 * Изменения схемы workflow (api-contract.md §5.2–5.4): пакет операций →
 * impact-preview → создание change → approve/reject администратором.
 */

export function impactPreview(
  workflowId: string,
  operations: WorkflowOperation[],
  signal?: AbortSignal,
): Promise<WorkflowChangeImpact> {
  return api.post<WorkflowChangeImpact>(`/workflows/${workflowId}/impact-preview`, {
    body: { operations },
    signal,
  });
}

export interface CreateChangePayload {
  comment?: string;
  operations: WorkflowOperation[];
}

export function createWorkflowChange(
  workflowId: string,
  payload: CreateChangePayload,
): Promise<WorkflowChangeRecord> {
  return api.post<WorkflowChangeRecord>(`/workflows/${workflowId}/changes`, {
    body: payload,
    idempotencyKey: crypto.randomUUID(),
  });
}

/** §5.2 не фиксирует конверт списка — терпимо принимаем массив и `{items}`. */
export async function listWorkflowChanges(
  workflowId: string,
  status?: WorkflowChangeStatus,
  signal?: AbortSignal,
): Promise<WorkflowChangeRecord[]> {
  const data = await api.get<WorkflowChangeRecord[] | { items: WorkflowChangeRecord[] }>(
    `/workflows/${workflowId}/changes`,
    { query: { status }, signal },
  );
  return Array.isArray(data) ? data : data.items;
}

export function getWorkflowChange(
  changeId: string,
  signal?: AbortSignal,
): Promise<WorkflowChangeRecord> {
  return api.get<WorkflowChangeRecord>(`/workflows/changes/${changeId}`, { signal });
}

/** Атомарное применение + миграция заявок (§5.4, только admin). */
export function approveWorkflowChange(changeId: string): Promise<WorkflowChangeRecord> {
  return api.post<WorkflowChangeRecord>(`/workflows/changes/${changeId}/approve`);
}

export function rejectWorkflowChange(
  changeId: string,
  reason: string,
): Promise<WorkflowChangeRecord> {
  return api.post<WorkflowChangeRecord>(`/workflows/changes/${changeId}/reject`, {
    body: { reason },
  });
}
