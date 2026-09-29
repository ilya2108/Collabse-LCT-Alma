/**
 * Ролевая модель (OVERVIEW.md Р-1, api-contract.md §12): четыре человеческие роли.
 * Сервисные роли (svc_notification, svc_integration) в UI не встречаются.
 */
export const APP_ROLES = ['admin', 'head_kam', 'kam', 'observer'] as const;

export type AppRole = (typeof APP_ROLES)[number];

export const ALL_ROLES: AppRole[] = [...APP_ROLES];

/** Роли с правом импорта (`/import` скрыт у observer — ux.md §3.2). */
export const IMPORT_ROLES: AppRole[] = ['admin', 'head_kam', 'kam'];

export const ADMIN_ONLY: AppRole[] = ['admin'];

export const ROLE_LABELS: Record<AppRole, string> = {
  admin: 'Администратор',
  head_kam: 'Руководитель КАМов',
  kam: 'КАМ',
  observer: 'Наблюдатель',
};

/** Порядок «старшинства» для выбора основной роли в шапке. */
const ROLE_PRIORITY: AppRole[] = ['admin', 'head_kam', 'kam', 'observer'];

export function isAppRole(value: string): value is AppRole {
  return (APP_ROLES as readonly string[]).includes(value);
}

/** Пересечение ролей пользователя со списком разрешённых. */
export function hasAnyRole(userRoles: readonly AppRole[], allowed: readonly AppRole[]): boolean {
  return allowed.some((role) => userRoles.includes(role));
}

export function primaryRole(userRoles: readonly AppRole[]): AppRole | null {
  return ROLE_PRIORITY.find((role) => userRoles.includes(role)) ?? null;
}
