import { api } from '../client';
import type {
  AdminRole,
  AdminRoleDetail,
  AdminUser,
  PermissionGroup,
  RoleCatalogItem,
  RoleCreateBody,
  RolePatchBody,
} from '../types';

/**
 * Роли и права (F2 §4.6): каталог ролей для всех, управление — право `admin:roles`.
 * Изменения применяются сразу, без перезахода пользователей: сервер сбрасывает
 * кэш каталога и рассылает SSE `access.changed` (Р-F2-8).
 */

/** Каталог ролей `GET /roles`: названия, процессы, способность выполнять переходы. */
export function listRoleCatalog(signal?: AbortSignal): Promise<RoleCatalogItem[]> {
  return api.get<{ items: RoleCatalogItem[] }>('/roles', { signal }).then((data) => data.items);
}

/** Каталог прав по группам `GET /admin/permissions`. */
export function listPermissions(signal?: AbortSignal): Promise<PermissionGroup[]> {
  return api
    .get<{ groups: PermissionGroup[] }>('/admin/permissions', { signal })
    .then((data) => data.groups);
}

export function listAdminRoles(signal?: AbortSignal): Promise<AdminRole[]> {
  return api.get<{ items: AdminRole[] }>('/admin/roles', { signal }).then((data) => data.items);
}

export function getAdminRole(id: string, signal?: AbortSignal): Promise<AdminRoleDetail> {
  return api.get<AdminRoleDetail>(`/admin/roles/${id}`, { signal });
}

/** Создание роли: ответ — роль без `users`/`coverage` (карточку перечитывает getAdminRole). */
export function createRole(body: RoleCreateBody): Promise<AdminRole> {
  return api.post<AdminRole>('/admin/roles', { body });
}

export function updateRole(id: string, body: RolePatchBody): Promise<AdminRole> {
  return api.patch<AdminRole>(`/admin/roles/${id}`, { body });
}

/** Сброс системной роли к правам по умолчанию из кода (кроме admin). */
export function resetRole(id: string, version: number): Promise<AdminRole> {
  return api.post<AdminRole>(`/admin/roles/${id}/reset`, { body: { version } });
}

/** Удаление своей роли: 409 `role_in_use`, если роль назначена или указана в переходах. */
export function deleteRole(id: string): Promise<void> {
  return api.delete<void>(`/admin/roles/${id}`);
}

/** Назначение ролей пользователю `PUT /admin/users/{id}/roles`. */
export function setUserRoles(
  userId: string,
  roles: string[],
  version?: number | null,
): Promise<AdminUser> {
  return api.put<AdminUser>(`/admin/users/${userId}/roles`, {
    body: { roles, version: version ?? null },
  });
}
