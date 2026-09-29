/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** База API без версии (например `/api` или `http://localhost:8000/api`); `/v1` добавляет клиент. */
  readonly VITE_API_URL?: string;
  /** Базовый URL Keycloak (например `http://localhost:8080` или `/auth` за ingress). */
  readonly VITE_KEYCLOAK_URL?: string;
  readonly VITE_KEYCLOAK_REALM?: string;
  readonly VITE_KEYCLOAK_CLIENT_ID?: string;
  /** Target прокси дев-сервера vite для `/api` (использует vite.config.ts). */
  readonly VITE_DEV_API_PROXY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
