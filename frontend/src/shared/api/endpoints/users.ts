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
