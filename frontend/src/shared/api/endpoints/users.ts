import { api } from '../client';
import type { PermissionCode } from '@/shared/auth/permissions';
import type { AdminUser, ListEnvelope } from '../types';

/**
 * Список пользователей CRM — `GET /admin/users` (api-contract.md §10.2), право
 * `admin:users`. Фильтры: `role` — код роли, `permission` — пользователи, у ролей
 * которых есть право (например, `requests:kam` — КАМы, включая свои роли «КАМ B2C»).
 */
export function listUsers(
  options: { role?: string; permission?: PermissionCode; signal?: AbortSignal } = {},
): Promise<ListEnvelope<AdminUser>> {
  return api.get<ListEnvelope<AdminUser>>('/admin/users', {
    query: { limit: 200, role: options.role, permission: options.permission },
    signal: options.signal,
  });
}

// --- Передача портфеля КАМа ------------------------------------------------------
// `POST /requests/transfer-portfolio`: открытые заявки (право `requests:assign_any` в
// процессе заявки) и закреплённые вузы (`universities:assign`) — другому сотруднику одним
// действием. `dry_run` — тот же отчёт без изменений (предпросмотр в диалоге). История,
// комментарии, файлы и авторство прошлых действий не меняются.

export interface PortfolioTransferPayload {
  from_user_id: string;
  to_user_id: string;
  include_universities: boolean;
  dry_run?: boolean;
}

export interface PortfolioTransferReport {
  dry_run: boolean;
  from_user: { id: string; full_name: string | null };
  to_user: { id: string; full_name: string | null };
  requests: {
    transferred: number;
    skipped: number;
    by_workflow: Partial<Record<'b2b' | 'b2c', number>>;
    items: Array<{ id: string; title: string; workflow_type: 'b2b' | 'b2c' }>;
    skipped_items: Array<{
      id: string;
      title: string;
      workflow_type: 'b2b' | 'b2c';
      reason: string;
    }>;
  };
  universities: {
    included: boolean;
    transferred: number;
    items: Array<{ id: string; name: string; is_active: boolean }>;
  };
}

export function transferPortfolio(
  payload: PortfolioTransferPayload,
  signal?: AbortSignal,
): Promise<PortfolioTransferReport> {
  return api.post<PortfolioTransferReport>('/requests/transfer-portfolio', {
    body: payload,
    signal,
  });
}
