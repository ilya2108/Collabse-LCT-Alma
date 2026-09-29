import { api } from '../client';
import type { AdminUser, ListEnvelope } from '../types';

/**
 * Список пользователей из PG-реплики Keycloak — `GET /admin/users`
 * (api-contract.md §10.2). Доступен admin (RU) и head_kam (R); остальные роли
 * его не зовут — селекты ответственного/КАМа показываются только этим ролям.
 */
export function listUsers(
  options: { role?: string; signal?: AbortSignal } = {},
): Promise<ListEnvelope<AdminUser>> {
  return api.get<ListEnvelope<AdminUser>>('/admin/users', {
    query: { limit: 200, role: options.role },
    signal: options.signal,
  });
}
