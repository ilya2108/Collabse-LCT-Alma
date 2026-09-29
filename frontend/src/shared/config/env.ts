/**
 * Конфигурация окружения фронтенда.
 *
 * Приоритет источников (deployment.md §2.2, §3.7):
 * 1. window.__ENV__ — рантайм-конфиг из /config.js (ConfigMap frontend-env),
 *    один docker-образ работает во всех окружениях;
 * 2. переменные сборки VITE_* (локальная разработка, `npm run dev`);
 * 3. дефолты закрытого контура (`/api` за ingress, Keycloak на localhost:8080 в dev).
 */

interface RuntimeEnv {
  API_BASE?: string;
  KEYCLOAK_URL?: string;
  KEYCLOAK_REALM?: string;
  KEYCLOAK_CLIENT_ID?: string;
}

declare global {
  interface Window {
    __ENV__?: RuntimeEnv;
  }
}

const runtime: RuntimeEnv = typeof window !== 'undefined' ? (window.__ENV__ ?? {}) : {};

function stripTrailingSlash(value: string): string {
  return value.endsWith('/') ? value.slice(0, -1) : value;
}

const apiBase = stripTrailingSlash(runtime.API_BASE ?? import.meta.env.VITE_API_URL ?? '/api');

export const env = {
  /** База API без версии, например `/api`. */
  apiBase,
  /** Полный префикс API v1: все запросы клиента идут на `${apiUrl}/...`. */
  apiUrl: `${apiBase}/v1`,
  keycloakUrl: stripTrailingSlash(
    runtime.KEYCLOAK_URL ?? import.meta.env.VITE_KEYCLOAK_URL ?? 'http://localhost:8080',
  ),
  keycloakRealm: runtime.KEYCLOAK_REALM ?? import.meta.env.VITE_KEYCLOAK_REALM ?? 'crm',
  keycloakClientId:
    runtime.KEYCLOAK_CLIENT_ID ?? import.meta.env.VITE_KEYCLOAK_CLIENT_ID ?? 'crm-frontend',
} as const;
