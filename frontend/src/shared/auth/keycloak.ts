import Keycloak from 'keycloak-js';
import { env } from '@/shared/config/env';

/**
 * Единственный экземпляр keycloak-js (OIDC Authorization Code + PKCE,
 * public client `crm-frontend` realm'а `crm` — api-contract.md §1.2).
 */
export const keycloak = new Keycloak({
  url: env.keycloakUrl,
  realm: env.keycloakRealm,
  clientId: env.keycloakClientId,
});

let initPromise: Promise<boolean> | null = null;

/**
 * Инициализация — ровно один раз (двойной mount эффектов в StrictMode
 * не должен вызывать повторный init, keycloak-js этого не переживает).
 *
 * Выученный урок: pkceMethod 'S256' считает challenge через Web Crypto
 * (crypto.subtle), а он существует только в secure context. Поэтому демо-стенд
 * живёт на http://crm.localhost — *.localhost по спеке «potentially trustworthy»
 * и резолвится Chromium'ом без /etc/hosts; на http://crm.local init падал.
 * Insecure context ловит guard в app/router/guards.tsx и предлагает переход.
 */
export function initKeycloak(): Promise<boolean> {
  initPromise ??= keycloak.init({
    onLoad: 'check-sso',
    pkceMethod: 'S256',
    silentCheckSsoRedirectUri: `${window.location.origin}/silent-check-sso.html`,
    checkLoginIframe: false,
  });
  return initPromise;
}

/**
 * Актуальный access token: тихо обновляет, если жить осталось < minValidity секунд.
 * null — пользователь не аутентифицирован или refresh не удался
 * (в этом случае keycloak-js вызовет onAuthRefreshError → модалка «Сессия истекла»).
 */
export async function getFreshToken(minValidity = 30): Promise<string | null> {
  if (!keycloak.authenticated) return null;
  try {
    await keycloak.updateToken(minValidity);
  } catch {
    return null;
  }
  return keycloak.token ?? null;
}

/** Редирект на страницу логина Keycloak с возвратом на указанный путь SPA. */
export function loginRedirect(redirectPath?: string): void {
  void keycloak.login({
    redirectUri: `${window.location.origin}${redirectPath ?? '/dashboard'}`,
  });
}

export function logoutRedirect(): void {
  void keycloak.logout({ redirectUri: `${window.location.origin}/login` });
}
