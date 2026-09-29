# Нагрузочное тестирование (k6)

Сценарий `k6-script.js` — прямой ответ на требование кейсодержателя (PLAN.md §2):
«свой отчёт нагрузочного тестирования … показать план масштабирования на 300+
одновременных пользователей». Методика и расчёт ёмкости — `docs/design/deployment.md` §8.

## Профиль нагрузки

| Ступень | Длительность | VU | Назначение |
|---|---|---|---|
| разгон | 2 мин | 0 → 300 | плавный выход на целевую нагрузку |
| полка | 5 мин | 300 | основное окно измерений SLA |
| пик | 1 мин | 300 → 400 | «300+»: запас прочности, триггер HPA |
| спад | 1 мин | 400 → 0 | проверка возврата HPA к 2 репликам |

Виртуальный пользователь = КАМ с think-time (5–15 с): канбан-доска →
реестр (заявки/вузы/студенты) → карточка заявки → у каждого 10-го VU — переход
по workflow (только валидный: список переходов запрашивается у движка,
`version` берётся из карточки; `409` — легитимный отказ optimistic locking).

**Thresholds (машинная проверка SLA, зашита в скрипт):**
`http_req_failed rate<0.01` · `http_req_duration p(95)<500 p(99)<1000` (мс) · `checks rate>0.99`.
k6 завершится ненулевым кодом, если SLA нарушен, — отчёт воспроизводим, а не скриншот.

## Предусловия

1. Стенд развёрнут и просижен: `make k8s-up && make demo` (сид гарантирует ≥ 50 заявок).
2. В `/etc/hosts` есть строка из `make hosts` (`crm.local`, `id.crm.local`, …).
3. Секрет нагрузочного клиента Keycloak (генерируется `make k8s-secrets`):

```sh
export LOADTEST_CLIENT_SECRET=$(kubectl -n crm get secret crm-keycloak-clients \
  -o jsonpath='{.data.LOADTEST_CLIENT_SECRET}' | base64 -d)
```

Аутентификация — выделенный confidential-клиент `crm-loadtest` (Direct Access
Grants включён **только** у него) под пользователем `kam1@demo` / `Demo2026!` —
обычная роль `kam`, без обхода RBAC.

## Запуск: локальный бинарь k6

```sh
make load-test
```

Цель сама достаёт `LOADTEST_CLIENT_SECRET` из секрета кластера (если переменная
не задана) и пишет артефакты в `docs/defense/`:
`load-raw.json` (посекундные метрики) и `load-summary.json` (итоговая сводка) —
оба в `.gitignore`; в git попадает только отчёт `docs/defense/load-report.md`.

## Запуск: k6 в Docker (бинарь не нужен)

```sh
docker run --rm -i \
  --add-host crm.local:host-gateway \
  --add-host id.crm.local:host-gateway \
  -e LOADTEST_CLIENT_SECRET="$LOADTEST_CLIENT_SECRET" \
  grafana/k6:0.57.0 run - < deploy/load/k6-script.js
```

- `--add-host …:host-gateway` направляет `crm.local`/`id.crm.local` из контейнера
  на хост — k3d-балансировщик публикует порт 80 на localhost хоста.
- Для minikube замените `host-gateway` на вывод `minikube -p crm ip`
  (или запустите `minikube tunnel` и оставьте `host-gateway`).
- Против удалённого стенда (nip.io / DNS организаторов) hosts-алиасы не нужны:
  `-e BASE_URL=http://crm.51.250.1.2.nip.io -e KC_URL=http://id.crm.51.250.1.2.nip.io`.

## Быстрый санити против compose-стека (SMOKE=1)

10 VU / 30 с вместо полного профиля — проверка, что конверт `{items}`, `version`
и переходы работают на живом дев-стенде (`make dev`):

```sh
docker run --rm --network crm-dev_default \
  -v "$PWD/deploy/load/k6-script.js":/script.js:ro \
  -e SMOKE=1 -e BASE_URL=http://backend:8000 -e KC_URL=http://keycloak:8080 \
  -e KC_HOST_HEADER=localhost:8080 \
  -e LOADTEST_CLIENT_SECRET="$LOADTEST_CLIENT_SECRET" \
  grafana/k6 run /script.js
```

`KC_HOST_HEADER` подменяет Host при запросе токена, чтобы `iss` совпал с
`KEYCLOAK_ISSUER` backend'а (`http://localhost:8080/realms/crm`).

## Переменные окружения скрипта

| Переменная | Дефолт | Назначение |
|---|---|---|
| `BASE_URL` | `http://crm.local` | базовый URL CRM (фронт `/`, API `/api`) |
| `KC_URL` | `http://id.crm.local` | Keycloak |
| `KC_REALM` | `crm` | realm |
| `LOADTEST_CLIENT_SECRET` | — (обязательна) | секрет клиента `crm-loadtest` |
| `LOADTEST_USER` | `kam1@demo` | пользователь прогона |
| `LOADTEST_USER_PASSWORD` | `Demo2026!` | его пароль |

## Что смотреть параллельно с прогоном

```sh
kubectl -n crm get hpa backend -w     # реплики backend: 2 → 4..6 на полке → 2 после спада
kubectl top pods -n crm               # CPU/Mem внутри limits (metrics-server)
```

Лог HPA и сводка k6 — фактура для `docs/defense/load-report.md`
(отчёт нагрузочного тестирования по требованию кейсодержателя).
