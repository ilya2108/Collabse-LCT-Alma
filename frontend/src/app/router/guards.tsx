import { TriangleAlert } from 'lucide-react';
import { motion, useReducedMotion } from 'motion/react';
import type { ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { ErrorBrokenIllustration } from '@/app/localIllustrations';
import { errorMessage, isApiError } from '@/shared/api/errors';
import { useAuth } from '@/shared/auth/AuthContext';
import type { AppRole } from '@/shared/auth/roles';
import { motionTokens } from '@/shared/lib/motion';
import { Button } from '@/shared/ui/button';

/**
 * Guard-экраны (redesign.md §6.1): сплеш — логотип с мягкой пульсацией
 * opacity (reduce → статично), ошибка Keycloak — состояние с иллюстрацией
 * `error-broken`. Логика guard'ов (ux.md §3.4, §5) не меняется.
 */

/** Полноэкранный сплеш на время keycloak.init (ux.md §5). */
export function AuthSplash(): ReactNode {
  const reduced = useReducedMotion() ?? false;
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-background">
      <motion.img
        src="/favicon.svg"
        alt="Альма"
        width={56}
        height={56}
        animate={reduced ? undefined : { opacity: [1, 0.45, 1] }}
        transition={{
          duration: motionTokens.duration.slow * 2,
          repeat: Infinity,
          ease: 'easeInOut',
        }}
      />
      <span className="text-sm text-muted-foreground" aria-live="polite">
        Загрузка…
      </span>
    </div>
  );
}

/** Канонический адрес демо-стенда (secure context, Chromium резолвит без /etc/hosts). */
const CANONICAL_ORIGIN = 'http://crm.localhost';

/**
 * Экран ошибки инициализации keycloak-js. Отдельная ветка для insecure context:
 * на http://*.local нет window.crypto.subtle (Web Crypto доступен только в secure
 * context), keycloak-js с PKCE S256 падает до первого сетевого запроса — «Повторить»
 * бессмысленно, нужен переход на канонический адрес http://crm.localhost.
 */
export function AuthErrorScreen({
  title,
  onRetry,
}: {
  title: string;
  onRetry?: () => void;
}): ReactNode {
  const insecureContext =
    typeof window !== 'undefined' && (!window.isSecureContext || !window.crypto?.subtle);

  if (insecureContext) {
    const target = `${CANONICAL_ORIGIN}${window.location.pathname}${window.location.search}`;
    return (
      <div className="flex min-h-screen items-center justify-center bg-background px-4">
        <div className="flex w-full max-w-md flex-col items-center gap-4 rounded-xl border border-border bg-card p-8 text-center shadow-card">
          <span className="flex size-12 items-center justify-center rounded-full bg-status-warning-tint text-status-warning-deep">
            <TriangleAlert className="size-6" aria-hidden="true" />
          </span>
          <h1 className="text-balance text-xl font-semibold">{title}</h1>
          <p className="text-sm text-muted-foreground">
            Откройте систему по адресу{' '}
            <a href={target} className="text-primary hover:underline">
              http://crm.localhost
            </a>{' '}
            — текущий адрес не является безопасным контекстом браузера, вход невозможен.
          </p>
          <Button asChild>
            <a href={target}>Открыть http://crm.localhost</a>
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <AuthErrorState title={title} onRetry={onRetry} />
    </div>
  );
}

/** Локальный ErrorState (§3.5-паттерн) для ошибок авторизации, без AntD. */
function AuthErrorState({ title, onRetry }: { title: string; onRetry?: () => void }): ReactNode {
  const error = new Error('Сервис авторизации недоступен');
  const traceId = isApiError(error) ? error.traceId : null;
  return (
    <div className="flex w-full max-w-md flex-col items-center gap-3 text-center">
      <ErrorBrokenIllustration height={160} />
      <h1 className="text-balance text-xl font-semibold">{title}</h1>
      <p className="text-sm text-muted-foreground">{errorMessage(error)}</p>
      {traceId ? (
        <p className="text-xs text-muted-foreground">Код обращения: {traceId}</p>
      ) : null}
      {onRetry ? <Button onClick={onRetry}>Повторить</Button> : null}
    </div>
  );
}

/**
 * Guard аутентификации поверх layout'а: неаутентифицированных уводит на /login
 * с сохранением исходного deep-link (вернёмся после логина).
 */
export function RequireAuth({ children }: { children: ReactNode }): ReactNode {
  const { phase, retry } = useAuth();
  const location = useLocation();

  switch (phase) {
    case 'initializing':
      return <AuthSplash />;
    case 'error':
      return <AuthErrorScreen title="Не удалось войти в систему" onRetry={retry} />;
    case 'anonymous':
      return (
        <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
      );
    default:
      return children;
  }
}

interface RequireRoleProps {
  roles: AppRole[];
  children: ReactNode;
}

/**
 * Guard маршрута по ролям (ux.md §3.4): прямой URL не по роли → редирект на /403.
 * Скрытие пунктов меню делает menuConfig; guard страхует deep-link'и.
 */
export function RequireRole({ roles, children }: RequireRoleProps): ReactNode {
  const { hasRole } = useAuth();
  if (!hasRole(...roles)) {
    return <Navigate to="/403" replace />;
  }
  return children;
}
