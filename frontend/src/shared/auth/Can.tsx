import type { ReactNode } from 'react';
import { useAuth } from './AuthContext';
import type { AppRole } from './roles';

interface CanProps {
  /** Показать детей, если у пользователя есть хотя бы одна из ролей. */
  roles?: AppRole[];
  /** Показать детей, если есть permission из /auth/me. */
  permission?: string;
  fallback?: ReactNode;
  children: ReactNode;
}

/**
 * Точечный RBAC-гейт для элементов UI (кнопка «Опубликовать», drag-handle):
 * пользователь никогда не видит кнопку, которую не может нажать (ux.md §1.1, §3.4).
 * Фронтовый RBAC — только UX-слой, сервер дублирует все проверки.
 */
export function Can({ roles, permission, fallback = null, children }: CanProps): ReactNode {
  const { hasRole, hasPermission } = useAuth();
  const roleOk = !roles || hasRole(...roles);
  const permissionOk = !permission || hasPermission(permission);
  return roleOk && permissionOk ? children : fallback;
}
