# frontend — React + Vite → nginx-unprivileged (см. docs/design/deployment.md §2.2)
# Один образ работает в compose, minikube и k3d без пересборки:
# рантайм-конфиг /config.js генерируется entrypoint-скриптом (envsubst) либо
# монтируется из ConfigMap frontend-env — смонтированный read-only файл имеет приоритет.

# --- build ---
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# --- runtime ---
FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY --from=build --chown=101:101 /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --chown=101:101 config.js.template /usr/share/nginx/html/config.js.template
COPY --chmod=755 docker/40-config-js.sh /docker-entrypoint.d/40-config-js.sh
# Дефолты соответствуют локальному docker compose --profile full
ENV KEYCLOAK_URL=http://localhost:8080 \
    KEYCLOAK_REALM=crm \
    KEYCLOAK_CLIENT_ID=crm-frontend \
    API_BASE=/api
EXPOSE 8080
