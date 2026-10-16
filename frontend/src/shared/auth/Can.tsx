import type { ReactNode } from 'react';
import type { WorkflowType } from '@/shared/api/types';
import { useAuth } from './AuthContext';
import type { PermissionCode } from './permissions';

interface CanProps {
  /** Показать детей, если есть право (с областью — в процессе `workflowType`). */
  permission?: PermissionCode;
  /** Показать детей, если есть хотя бы одно из прав. */
  anyOf?: PermissionCode[];
  /** Процесс для прав с областью (B2B/B2C). */
  workflowType?: WorkflowType | null;
  fallback?: ReactNode;
  children: ReactNode;
}

/**
 * Точечный гейт по правам для элементов UI (кнопка «Опубликовать», drag-handle):
 * пользователь никогда не видит кнопку, которую не может нажать (ux.md §1.1, §3.4).
 * Фронтовый RBAC — только UX-слой, сервер дублирует все проверки.
 */
export function Can({
  permission,
  anyOf,
  workflowType,
  fallback = null,
  children,
}: CanProps): ReactNode {
  const { hasPermission } = useAuth();
  const permissionOk = !permission || hasPermission(permission, workflowType);
  const anyOk =
    !anyOf || anyOf.length === 0 || anyOf.some((code) => hasPermission(code, workflowType));
  return permissionOk && anyOk ? children : fallback;
}
