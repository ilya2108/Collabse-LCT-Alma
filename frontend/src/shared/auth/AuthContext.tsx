import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { fetchMe } from '@/shared/api/endpoints/auth';
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
import type { CurrentUser } from '@/shared/api/types';
import {
  hasAnyRole,
  isAppRole,
  primaryRole,
  type AppRole,
} from './roles';
import { initKeycloak, keycloak, loginRedirect, logoutRedirect } from './keycloak';

/**
 * Контекст аутентификации: keycloak-js (PKCE, check-sso) + профиль из GET /auth/me.
 * Роли и permissions в UI берутся из /auth/me — единственный источник истины
 * для отрисовки интерфейса по ролевой модели (api-contract.md §2.1).
 */

export type AuthPhase =
  /** Идёт keycloak.init (check-sso) или загрузка профиля. */
  | 'initializing'
  /** Не аутентифицирован — доступен только /login. */
  | 'anonymous'
  /** Аутентифицирован, профиль загружен. */
  | 'ready'
  /** Keycloak или /auth/me недоступны. */
  | 'error'
  /** Refresh-токен истёк — модалка «Сессия истекла» (ux.md §5). */
  | 'expired';

export interface AuthContextValue {
  phase: AuthPhase;
  user: CurrentUser | null;
  /** Только прикладные роли (admin/head_kam/kam/observer). */
  roles: AppRole[];
  /** Старшая роль для тега в шапке. */
  mainRole: AppRole | null;
  hasRole: (...allowed: AppRole[]) => boolean;
  hasPermission: (permission: string) => boolean;
  login: (redirectPath?: string) => void;
  logout: () => void;
  /** Повторить инициализацию после ошибки. */
  retry: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }): ReactNode {
  const [phase, setPhase] = useState<AuthPhase>('initializing');
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [attempt, setAttempt] = useState(0);

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
        const profile = await fetchMe();
        if (cancelled) return;
        setUser(profile);
        setPhase('ready');
        sseClient.connect();
      })
      .catch(() => {
        if (!cancelled) setPhase('error');
      });

    return () => {
      cancelled = true;
    };
  }, [attempt]);

  useEffect(() => {
    if (phase !== 'ready') sseClient.close();
  }, [phase]);

  const roles = useMemo<AppRole[]>(
    () => (user?.roles ?? []).filter(isAppRole),
    [user],
  );

  const hasRole = useCallback(
    (...allowed: AppRole[]) => hasAnyRole(roles, allowed),
    [roles],
  );

  const hasPermission = useCallback(
    (permission: string) => user?.permissions.includes(permission) ?? false,
    [user],
  );

  const retry = useCallback(() => {
    setPhase('initializing');
    setAttempt((n) => n + 1);
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      phase,
      user,
      roles,
      mainRole: primaryRole(roles),
      hasRole,
      hasPermission,
      login: loginRedirect,
      logout: logoutRedirect,
      retry,
    }),
    [phase, user, roles, hasRole, hasPermission, retry],
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
