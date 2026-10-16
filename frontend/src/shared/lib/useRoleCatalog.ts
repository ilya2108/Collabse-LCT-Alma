import { useQuery } from '@tanstack/react-query';
import { useCallback, useMemo } from 'react';
import { listRoleCatalog } from '@/shared/api/endpoints/roles';
import type { RoleCatalogItem, WorkflowType } from '@/shared/api/types';
import { useAuth } from '@/shared/auth/AuthContext';
import { fallbackRoleName } from '@/shared/auth/roles';

/** Ключ кэша каталога ролей — его инвалидирует AccessChangeListener по `access.changed`. */
export const ROLE_CATALOG_QUERY_KEY = ['role-catalog'] as const;

export interface RoleCatalog {
  roles: RoleCatalogItem[];
  byCode: Map<string, RoleCatalogItem>;
  /** Название роли: из каталога, иначе запасная подпись системной роли или код. */
  nameOf: (code: string) => string;
  /** Роли, способные выполнять переходы в процессе (право requests:transition). */
  transitionCapable: (workflowType: WorkflowType) => RoleCatalogItem[];
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => void;
}

/**
 * Каталог ролей `GET /roles` (F2): названия своих ролей, процессы и способность
 * выполнять переходы — для бейджей ролей, конструктора процессов и назначения
 * ролей. Кэш 60 с; изменения ролей приходят по SSE `access.changed`.
 */
export function useRoleCatalog(): RoleCatalog {
  const { phase } = useAuth();
  const query = useQuery({
    queryKey: ROLE_CATALOG_QUERY_KEY,
    queryFn: ({ signal }) => listRoleCatalog(signal),
    enabled: phase === 'ready',
    staleTime: 60_000,
  });
  const roles = useMemo(() => query.data ?? [], [query.data]);
  const byCode = useMemo(() => new Map(roles.map((role) => [role.code, role])), [roles]);

  const nameOf = useCallback(
    (code: string) => byCode.get(code)?.name ?? fallbackRoleName(code),
    [byCode],
  );
  const transitionCapable = useCallback(
    (workflowType: WorkflowType) =>
      roles.filter((role) => role.transition_workflow_types.includes(workflowType)),
    [roles],
  );
  const { refetch } = query;

  return {
    roles,
    byCode,
    nameOf,
    transitionCapable,
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    refetch: () => void refetch(),
  };
}
