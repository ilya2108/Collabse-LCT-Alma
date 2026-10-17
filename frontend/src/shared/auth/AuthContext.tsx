import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { fetchMe } from '@/shared/api/endpoints/auth';
import { isApiError } from '@/shared/api/errors';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/shared/ui/alert-dialog';
import { sseClient } from '@/shared/api/sse';
import type {
  AccessInfo,
  CurrentUser,
  RecordScope,
  RoleRef,
  WorkflowType,
} from '@/shared/api/types';
import {
  SCOPED_PERMISSIONS,
  WORKFLOW_TYPES,
  deriveProfile,
  isReadOnly,
  type AccessProfile,
  type PermissionCode,
} from './permissions';
import { SYSTEM_ROLE_CODES, fallbackRoleName } from './roles';
import { initKeycloak, keycloak, loginRedirect, logoutRedirect } from './keycloak';

/**
 * Контекст аутентификации: keycloak-js (PKCE, check-sso) + профиль из GET /auth/me.
 * Интерфейс строится по правам, а не по ролям (F2): `hasPermission(code, wf)` —
 * права с областью проверяются в конкретном процессе (B2B/B2C), общие — в целом.
 * Профиль перечитывается без перезахода по SSE `access.changed` (AccessChangeListener).
 * Фронтовый RBAC — только UX-слой, сервер дублирует все проверки.
 */

export type AuthPhase =
  /** Идёт keycloak.init (check-sso) или загрузка профиля. */
  | 'initializing'
  /** Не аутентифицирован — доступен только /login. */
  | 'anonymous'
  /** Аутентифицирован, профиль загружен. */
  | 'ready'
  /** Keycloak или /auth/me недоступны (или 403 — учётной записи не назначены роли). */
  | 'error'
  /** Refresh-токен истёк — модалка «Сессия истекла» (ux.md §5). */
  | 'expired';

export interface AuthContextValue {
  phase: AuthPhase;
  user: CurrentUser | null;
  /** Причина phase === 'error': ApiError 403 — вход в CRM закрыт (нет ролей, деактивирован). */
  error: unknown;
  /**
   * Есть ли право. Право с областью при заданном процессе проверяется в нём,
   * без процесса — «хотя бы в одном процессе»; у общего права процесс не важен.
   */
  hasPermission: (code: PermissionCode, workflowType?: WorkflowType | null) => boolean;
  /** Хотя бы одно из прав (без учёта процесса). */
  hasAny: (...codes: PermissionCode[]) => boolean;
  /** Процессы, в которых действует право (в порядке B2B, B2C). */
  workflowTypesWith: (code: PermissionCode) => WorkflowType[];
  /** Процессы с правом просмотра заявок. */
  workflowTypes: WorkflowType[];
  /** Область записей в процессе: все заявки или только свои; null — процесс недоступен. */
  recordsScope: (workflowType: WorkflowType) => RecordScope | null;
  /** Коды ролей пользователя, способных выполнять переходы в процессе. */
  transitionRoles: (workflowType: WorkflowType) => string[];
  /** Коды ролей CRM пользователя. */
  roleCodes: string[];
  /** Названия ролей (из каталога ролей) в том же порядке. */
  roleNames: string[];
  /** Профиль интерфейса (онбординг, подсказки) — выводится из прав. */
  profile: AccessProfile | null;
  /** Ни одного права на изменение данных — тег «Режим просмотра». */
  readOnly: boolean;
  /** Перечитать /auth/me (после изменения ролей администратором). */
  reloadProfile: () => Promise<void>;
  login: (redirectPath?: string) => void;
  logout: () => void;
  /** Повторить инициализацию после ошибки. */
  retry: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

/**
 * Запасной блок `access` для backend без F2: все права действуют в обоих
 * процессах и на все заявки — прежнее поведение интерфейса.
 */
function legacyAccess(permissions: string[], roles: string[]): AccessInfo {
  const scoped = permissions.filter((code) => SCOPED_PERMISSIONS.has(code));
  return {
    workflow_types: [...WORKFLOW_TYPES],
    records: { b2b: 'all', b2c: 'all' },
    by_workflow: { b2b: scoped, b2c: scoped },
    transition_roles: { b2b: roles, b2c: roles },
    scoped_permissions: [...SCOPED_PERMISSIONS],
  };
}

/** Профиль с гарантированными полями F2 (`role_details`, `access`). */
function normalizeProfile(raw: CurrentUser): CurrentUser {
  const roles = raw.roles ?? [];
  const permissions = raw.permissions ?? [];
  const roleDetails: RoleRef[] =
    raw.role_details ??
    roles.map((code) => ({
      code,
      name: fallbackRoleName(code),
      is_system: (SYSTEM_ROLE_CODES as readonly string[]).includes(code),
    }));
  return {
    ...raw,
    roles,
    permissions,
    role_details: roleDetails,
    access: raw.access ?? legacyAccess(permissions, roles),
  };
}

export function AuthProvider({ children }: { children: ReactNode }): ReactNode {
  const [phase, setPhase] = useState<AuthPhase>('initializing');
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [attempt, setAttempt] = useState(0);
  /** Номер последнего запроса профиля: устаревший ответ не перетирает свежий. */
  const profileSeqRef = useRef(0);

  useEffect(() => {
    let cancelled = false;

    keycloak.onTokenExpired = () => {
      keycloak.updateToken(30).catch(() => undefined);
    };
    keycloak.onAuthRefreshError = () => {
      if (!cancelled) setPhase('expired');
    };

    initKeycloak()
      .then(async (authenticated) => {
        if (cancelled) return;
        if (!authenticated) {
          setPhase('anonymous');
          return;
        }
        const seq = ++profileSeqRef.current;
        const profile = normalizeProfile(await fetchMe());
        if (cancelled || seq !== profileSeqRef.current) return;
        setUser(profile);
        setError(null);
        setPhase('ready');
        sseClient.connect();
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err);
        setPhase('error');
      });

    return () => {
      cancelled = true;
    };
  }, [attempt]);

  useEffect(() => {
    if (phase !== 'ready') sseClient.close();
  }, [phase]);

  const reloadProfile = useCallback(async () => {
    const seq = ++profileSeqRef.current;
    try {
      const profile = normalizeProfile(await fetchMe());
      if (seq !== profileSeqRef.current) return;
      setUser(profile);
    } catch (err) {
      if (seq !== profileSeqRef.current) return;
      // 403: роли сняты или учётная запись деактивирована — вход в CRM закрыт.
      // Прочие ошибки (сеть) — остаёмся на прежнем профиле до следующего события.
      if (isApiError(err) && err.status === 403) {
        setError(err);
        setPhase('error');
      }
    }
  }, []);

  // --- Производные от профиля -------------------------------------------------
  const permissionSet = useMemo(() => new Set(user?.permissions ?? []), [user]);
  const scopedSet = useMemo(
    () =>
      user?.access.scoped_permissions?.length
        ? new Set(user.access.scoped_permissions)
        : SCOPED_PERMISSIONS,
    [user],
  );
  const byWorkflow = useMemo(() => {
    const map = new Map<WorkflowType, Set<string>>();
    for (const wf of WORKFLOW_TYPES) {
      map.set(wf, new Set(user?.access.by_workflow[wf] ?? []));
    }
    return map;
  }, [user]);

  const hasPermission = useCallback(
    (code: PermissionCode, workflowType?: WorkflowType | null) => {
      if (workflowType && scopedSet.has(code)) {
        return byWorkflow.get(workflowType)?.has(code) ?? false;
      }
      return permissionSet.has(code);
    },
    [permissionSet, scopedSet, byWorkflow],
  );

  const hasAny = useCallback(
    (...codes: PermissionCode[]) => codes.some((code) => permissionSet.has(code)),
    [permissionSet],
  );

  const workflowTypesWith = useCallback(
    (code: PermissionCode) => WORKFLOW_TYPES.filter((wf) => hasPermission(code, wf)),
    [hasPermission],
  );

  const workflowTypes = useMemo(() => workflowTypesWith('requests:read'), [workflowTypesWith]);

  const recordsScope = useCallback(
    (workflowType: WorkflowType): RecordScope | null => {
      if (!user) return null;
      const records = user.access.records[workflowType];
      if (records) return records;
      return byWorkflow.get(workflowType)?.has('requests:read') ? 'all' : null;
    },
    [user, byWorkflow],
  );

  const transitionRoles = useCallback(
    (workflowType: WorkflowType): string[] => user?.access.transition_roles[workflowType] ?? [],
    [user],
  );

  const roleCodes = useMemo(() => user?.roles ?? [], [user]);
  const roleNames = useMemo(
    () => (user?.role_details ?? []).map((role) => role.name || fallbackRoleName(role.code)),
    [user],
  );
  const profile = useMemo(
    () => (user ? deriveProfile((code) => permissionSet.has(code)) : null),
    [user, permissionSet],
  );
  const readOnly = useMemo(
    () => Boolean(user) && isReadOnly((code) => permissionSet.has(code)),
    [user, permissionSet],
  );

  const retry = useCallback(() => {
    setPhase('initializing');
    setError(null);
    setAttempt((n) => n + 1);
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      phase,
      user,
      error,
      hasPermission,
      hasAny,
      workflowTypesWith,
      workflowTypes,
      recordsScope,
      transitionRoles,
      roleCodes,
      roleNames,
      profile,
      readOnly,
      reloadProfile,
      login: loginRedirect,
      logout: logoutRedirect,
      retry,
    }),
    [
      phase,
      user,
      error,
      hasPermission,
      hasAny,
      workflowTypesWith,
      workflowTypes,
      recordsScope,
      transitionRoles,
      roleCodes,
      roleNames,
      profile,
      readOnly,
      reloadProfile,
      retry,
    ],
  );

  return (
    <AuthContext.Provider value={value}>
      {children}
      {/* Модалка «Сессия истекла» (ux.md §5): AlertDialog — без закрытия по Esc/фону. */}
      <AlertDialog open={phase === 'expired'}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Сессия истекла</AlertDialogTitle>
            <AlertDialogDescription>
              Время сессии вышло. Войдите снова, чтобы продолжить работу — несохранённые
              черновики конструктора сохраняются локально и не потеряются.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogAction onClick={() => loginRedirect(window.location.pathname)}>
              Войти заново
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth используется вне AuthProvider');
  }
  return ctx;
}
