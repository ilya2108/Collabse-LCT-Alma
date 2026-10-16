# Нагрузочное тестирование (k6)

Скрипт `k6-script.js` содержит четыре профиля, профиль выбирается переменной `SCENARIO`:

| `SCENARIO` | Что проверяет | Цель make |
|---|---|---|
| `steady` (по умолчанию) | 300+ одновременных пользователей, план масштабирования (требование кейсодержателя, PLAN.md §2) | `make load-test` |
| `users50` | НФТ ТЗ: 50 параллельных пользователей под 5+ разными учётками | `make load-test-users` |
| `reports` | НФТ ТЗ по одновременным отчётам: от 10 сразу, лёгкие и тяжёлые вперемешку; ответ ≤ 1 с при смене выборки | `make load-test-reports` |
| `nft` | `users50` и `reports` одновременно: 10 отчётов на фоне 50 пользователей | `make load-test-nft` |

Методика и расчёт ёмкости — `docs/design/deployment.md` §8, методика профилей НФТ —
раздел «Методика НФТ» ниже. Результаты — `docs/evidence/load-report.md`.

## Профиль `steady`

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

## Методика НФТ: 50 пользователей и 10 параллельных отчётов

Методика зафиксирована до прогона. Результатов в этом разделе нет: их пишем в
`docs/evidence/load-report.md` только по фактическому прогону, с датой, стендом и сводкой k6.

**Подготовка.** Перед каждым прогоном стенд сбрасывают к эталону: `make demo-reset`.
Данные тогда считаются от сегодняшнего дня, около 116 заявок, 56 вузов и 20 КАМов, роли
исходные. После прогона стенд сбрасывают ещё раз: профили делают переходы и выгрузки.

**`users50` — 50 пользователей.** 50 VU, по умолчанию 5 минут на постоянной нагрузке
(`DURATION`). Каждый VU работает под одной из учёток `LOADTEST_USERS` по кругу
(`kam1`, `kam2`, `head`, `admin`, `observer` — по 10 VU на учётку). Значит, работают
разные роли и разные области видимости, а кэш не общий на всех. Путь VU — путь КАМа из
`steady`: доска → реестр → карточка → у каждого 10-го VU переход. Think-time 5–15 с. Если
учёток меньше пяти, скрипт останавливается в `setup()`.

**`reports` — 10 параллельных отчётов.** 10 VU без разгона, каждый всё время строит
отчёты:

- соседние VU в один момент строят разные отчёты: 8 кодов дашборда (`/reports/dashboard`,
  `funnel`, `kam-workload`, `stuck`, `universities-coverage`, `programs-demand`,
  `talent-pool-funnel`, `dynamics`), «Активность КАМов» и её сводку
  (`/reports/kam-activity`, `/reports/kam-activity/summary`). Новые отчёты добавляются
  через `EXTRA_REPORTS=interactions`;
- кэш холодный. Период случайный: конец от 0 до 10 дней назад, длина от 7 до 120 дней.
  Фильтры (процесс, КАМ, вуз, продукт, порог зависания) меняются от итерации к итерации.
  Ключ кэша отчёта — хеш параметров и области роли, поэтому почти каждый запрос считается
  по базе, а не берётся из KeyDB;
- отчёты строят разные роли (`REPORT_USERS`): руководитель, администратор, наблюдатель
  (все КАМы) и два КАМа;
- каждая третья итерация делает выгрузку `POST /export/jobs` с теми же фильтрами и скачивает
  файл. Варианты по кругу: XLSX и PDF «Активности КАМов», XLSX дашборда, XLSX реестра заявок;
- пауза между отчётами 1–3 с: аналитик смотрит результат и меняет фильтр.

**`nft` — оба профиля одновременно.** Главный прогон для отчёта по НФТ: 50 пользователей
и 10 отчётов в одном окне 5 минут.

**Что считаем и с чем сравниваем.** Пороги заложены в скрипт, при нарушении k6 завершится
ненулевым кодом:

| Метрика | Порог | Откуда порог |
|---|---|---|
| `http_req_failed` | < 1 % | SLA кейсодержателя |
| `checks` | > 99 % | — |
| `http_req_duration{kind:ui}` | p95 < 500 мс, p99 < 1000 мс | НФТ «не более 1 секунды» для операций пользователя |
| `crm_report_ms` (построение отчёта) | p95 < 1000 мс | НФТ «не более 1 секунды» при смене выборки |
| `crm_export_ms` (выгрузка + скачивание) | p95 < 3000 мс | цель команды, в ТЗ не нормируется |

В отчёт идут p50, p95 и p99 по каждому коду отчёта (тег `name`) и по выгрузкам (теги
`entity`, `format`). Ещё в отчёт идут доля ошибок с разбивкой по кодам ответа, число
реплик backend (`kubectl -n crm get hpa backend -w`) и пик соединений PostgreSQL:

```sh
kubectl -n crm exec statefulset/postgres -- \
  sh -c 'psql -U "$POSTGRES_USER" -d crm -Atc "select count(*) from pg_stat_activity"'
```

Бюджет соединений: пул на процесс 4 + 2, `max_connections=150`, расчёт —
`docs/evidence/load-report.md` §5.

**Ограничения методики.** Генератор и стенд работают на одной машине. Токены выдаются один
раз в `setup()` (срок жизни 30 минут), поэтому прогон длиннее 25 минут потребует
перелогина. Выгрузки создают файлы в MinIO: после прогона нужен `make demo-reset`.

## Предусловия

1. Стенд развёрнут и сброшен к эталону: `make k8s-up && make demo-reset` (≥ 50 заявок).
2. В `/etc/hosts` есть строка из `make hosts` (`crm.local`, `id.crm.local`, …).
3. Секрет нагрузочного клиента Keycloak (генерируется `make k8s-secrets`):

```sh
export LOADTEST_CLIENT_SECRET=$(kubectl -n crm get secret crm-keycloak-clients \
  -o jsonpath='{.data.LOADTEST_CLIENT_SECRET}' | base64 -d)
```

Аутентификация — выделенный confidential-клиент `crm-loadtest` (Direct Access
Grants включён **только** у него). `steady` работает под `kam1@demo`, профили НФТ — под
пятью демо-учётками realm (пароль `Demo2026!`). Роли обычные, RBAC не обходится.

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
- Профиль НФТ в docker: добавьте `-e SCENARIO=nft` (k6 читает переменные окружения
  контейнера).
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
| `LOADTEST_USER` | `kam1@demo` | пользователь профиля `steady` |
| `LOADTEST_USER_PASSWORD` | `Demo2026!` | пароль демо-учёток |
| `SCENARIO` | `steady` | `steady` \| `users50` \| `reports` \| `nft` |
| `LOADTEST_USERS` | `kam1@demo,kam2@demo,head@demo,admin@demo,observer@demo` | учётки `users50` (не меньше 5) |
| `REPORT_USERS` | `head@demo,admin@demo,observer@demo,kam1@demo,kam2@demo` | кто строит отчёты |
| `USERS_VUS` / `REPORT_VUS` | `50` / `10` | число VU профилей |
| `DURATION` | `5m` | длительность профилей НФТ |
| `EXTRA_REPORTS` | — | дополнительные коды `/reports/{code}` через запятую |

## Что смотреть параллельно с прогоном

```sh
kubectl -n crm get hpa backend -w     # реплики backend: 2 → 4..6 на полке → 2 после спада
kubectl top pods -n crm               # CPU/Mem внутри limits (metrics-server)
```

Лог HPA и сводка k6 — фактура для `docs/defense/load-report.md`
(отчёт нагрузочного тестирования по требованию кейсодержателя).
