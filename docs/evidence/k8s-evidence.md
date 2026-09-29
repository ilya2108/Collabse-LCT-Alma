# Доказательства развёртывания в Kubernetes (k3d)

Снято с живого кластера `k3d-crm` (k3s v1.35.5, 1 server + 1 agent) 2026-09-16.
Развёртывание: `make k8s-up` → build 5 образов `crm/*:dev` → `k3d image import` →
`kubectl apply -k deploy/k8s/overlays/k3d` → Jobs (db-migrate, minio-init, seed-demo) → rollout.
Доступ снаружи — через k3d serverlb (порт 80 → traefik), проверки ниже шли без /etc/hosts:
`curl --resolve 'crm.local:80:127.0.0.1' …`.

## Узлы

```
NAME               STATUS   ROLES           AGE   VERSION        INTERNAL-IP   EXTERNAL-IP   OS-IMAGE           KERNEL-VERSION     CONTAINER-RUNTIME
k3d-crm-agent-0    Ready    <none>          60m   v1.35.5+k3s1   172.19.0.4    <none>        K3s v1.35.5+k3s1   6.11.11-linuxkit   containerd://2.2.3-k3s1
k3d-crm-server-0   Ready    control-plane   61m   v1.35.5+k3s1   172.19.0.3    <none>        K3s v1.35.5+k3s1   6.11.11-linuxkit   containerd://2.2.3-k3s1
```

## Поды (kubectl get pods -n crm -o wide)

Все 8 сервисов работают, 4 Job'а — Completed (включая stuck-demo, созданный
из CronJob stuck-check); поды разложены по двум узлам
(backend — 2 реплики на разных узлах: отказоустойчивость + балансировка).

```
NAME                            READY   STATUS      RESTARTS   AGE     IP           NODE               NOMINATED NODE   READINESS GATES
backend-68648c88cf-4cjxb        1/1     Running     0          2m58s   10.42.0.27   k3d-crm-server-0   <none>           <none>
backend-68648c88cf-69qkl        1/1     Running     0          2m50s   10.42.1.29   k3d-crm-agent-0    <none>           <none>
cms-stub-7f9b79fcfb-nnp9q       1/1     Running     0          60m     10.42.1.3    k3d-crm-agent-0    <none>           <none>
db-migrate-6zztb                0/1     Completed   0          3m14s   10.42.0.24   k3d-crm-server-0   <none>           <none>
frontend-65776bc47d-2fmmq       1/1     Running     0          2m58s   10.42.1.27   k3d-crm-agent-0    <none>           <none>
keycloak-77f768b965-qtm7f       1/1     Running     0          9m44s   10.42.1.22   k3d-crm-agent-0    <none>           <none>
keydb-79cf89b9cb-f4zcg          1/1     Running     0          9m44s   10.42.1.23   k3d-crm-agent-0    <none>           <none>
lms-stub-5cc66899c9-bhk2d       1/1     Running     0          60m     10.42.0.9    k3d-crm-server-0   <none>           <none>
minio-0                         1/1     Running     0          9m43s   10.42.0.23   k3d-crm-server-0   <none>           <none>
minio-init-cs9ls                0/1     Completed   0          3m14s   10.42.1.26   k3d-crm-agent-0    <none>           <none>
notification-6445d87467-sbbz6   1/1     Running     0          2m58s   10.42.1.28   k3d-crm-agent-0    <none>           <none>
postgres-0                      1/1     Running     0          60m     10.42.0.15   k3d-crm-server-0   <none>           <none>
seed-demo-294rw                 0/1     Completed   0          3m14s   10.42.0.25   k3d-crm-server-0   <none>           <none>
stuck-demo-7lqst                0/1     Completed   0          2m30s   10.42.0.28   k3d-crm-server-0   <none>           <none>
```

## Jobs (kubectl get jobs -n crm)

Миграции (Alembic → head), init бакетов/ключей MinIO и идемпотентный сид демо-данных —
все завершены успешно. Реплики backend схему не трогают: их initContainer только ждёт head.

```
NAME         STATUS     COMPLETIONS   DURATION   AGE
db-migrate   Complete   1/1           7s         3m14s
minio-init   Complete   1/1           8s         3m14s
seed-demo    Complete   1/1           8s         3m14s
stuck-demo   Complete   1/1           7s         2m31s
```

`stuck-demo` — ручной запуск CronJob stuck-check (`kubectl create job --from=cronjob/stuck-check`),
ответ backend: `{"checked":7,"stuck_found":3,"newly_stuck":3,"notifications_sent":3,"escalations_sent":1}`.

## Ingress (kubectl get ingress -n crm)

Один Ingress, 5 хостов (crm.local + /api, id.crm.local, lms.crm.local, cms.crm.local,
minio.crm.local), класс traefik (встроен в k3s), опубликован на обоих узлах.

```
NAME   CLASS     HOSTS                                              ADDRESS                 PORTS   AGE
crm    traefik   crm.local,id.crm.local,lms.crm.local + 2 more...   172.19.0.3,172.19.0.4   80      60m
```

## HPA (kubectl get hpa -n crm)

Ответ на требование «300+ одновременных пользователей»: 2 → 6 реплик backend по CPU 70 %.
TARGETS показывает живую метрику — metrics-server (встроен в k3s) работает.

```
NAME      REFERENCE            TARGETS       MINPODS   MAXPODS   REPLICAS   AGE
backend   Deployment/backend   cpu: 8%/70%   2         6         2          60m
```

kubectl top pods -n crm (подтверждение потока метрик):

```
NAME                            CPU(cores)   MEMORY(bytes)
backend-68648c88cf-4cjxb        24m          225Mi
backend-68648c88cf-69qkl        17m          221Mi
cms-stub-7f9b79fcfb-nnp9q       4m           41Mi
frontend-65776bc47d-2fmmq       1m           9Mi
keycloak-77f768b965-qtm7f       18m          449Mi
keydb-79cf89b9cb-f4zcg          18m          8Mi
lms-stub-5cc66899c9-bhk2d       4m           41Mi
minio-0                         3m           239Mi
notification-6445d87467-sbbz6   5m           179Mi
postgres-0                      16m          59Mi
```

## NetworkPolicy (kubectl get networkpolicy -n crm)

Default-deny + явная матрица связей (../design/deployment.md §3.5): DNS всем, ingress-трафик
только фронтовым сервисам, данные (PG/KeyDB/MinIO) — только тем, кому положено,
notification в DMZ (наружу — только Telegram API и keydb), заглушки принимают
трафик только от backend/ingress (stubs-ingress — ingress-половина пары к stubs-egress:
без неё доставка outbox-вебхуков backend→ЛМС/CMS блокировалась бы default-deny).

```
NAME                 POD-SELECTOR                                                                    AGE
allow-dns            <none>                                                                          60m
allow-from-ingress   app.kubernetes.io/name in (backend,cms-stub,frontend,keycloak,lms-stub,minio)   60m
backend              app.kubernetes.io/name=backend                                                  60m
default-deny-all     <none>                                                                          60m
jobs-egress          role=job                                                                        60m
keycloak             app.kubernetes.io/name=keycloak                                                 60m
keydb                app.kubernetes.io/name=keydb                                                    60m
minio                app.kubernetes.io/name=minio                                                    60m
notification-dmz     app.kubernetes.io/name=notification                                             60m
postgres             app.kubernetes.io/name=postgres                                                 60m
stubs-egress         app.kubernetes.io/name in (cms-stub,lms-stub)                                   60m
stubs-ingress        app.kubernetes.io/name in (cms-stub,lms-stub)                                   9m44s
```

## CronJob (kubectl get cronjob -n crm)

Проверка зависших заявок — ежечасно; очистка «мягко удалённых» — ночью.
На демо триггерится вручную: `make demo` (kubectl create job --from=cronjob/stuck-check).
curl в обоих CronJob ходит с `--retry-connrefused`: сетевой контроллер k3s (kube-router)
применяет allow-правила NetworkPolicy к свежему поду Job с задержкой ~секунды,
и мгновенный первый connect мог получить reject (наблюдалось на живом стенде).

```
NAME            SCHEDULE     TIMEZONE   SUSPEND   ACTIVE   LAST SCHEDULE   AGE
purge-deleted   30 3 * * *   <none>     False     0        <none>          60m
stuck-check     0 * * * *    <none>     False     0        40m             60m
```

## Сквозной smoke через ingress (без /etc/hosts, `curl --resolve …:80:127.0.0.1`)

| Проверка | Результат |
|---|---|
| `GET http://crm.local/` | 200, index.html SPA («Альма») |
| `GET http://crm.local/config.js` | 200, runtime-конфиг: KEYCLOAK_URL=http://id.crm.local, realm crm, client crm-frontend, API_BASE=/api |
| `GET http://crm.local/api/v1/health/live` | 200 `{"status":"ok"}` |
| `GET http://crm.local/api/v1/health/ready` | 200 `{"status":"ok","checks":{"postgres":true,"keydb":true}}` |
| `GET http://id.crm.local/realms/crm/.well-known/openid-configuration` | 200, issuer `http://id.crm.local/realms/crm` |
| Password grant (crm-loadtest, kam1@demo) через id.crm.local | 200, access_token выдан |
| `GET http://crm.local/api/v1/auth/me` с этим токеном | 200, kam1@demo, роль kam — iss внешнего токена валиден для backend (KC_HOSTNAME + KEYCLOAK_ISSUER согласованы) |
| `GET http://crm.local/api/v1/requests/board` | 200, канбан с колонками и заявками |
| `POST .../requests/{id}/transitions` («Взять в работу», токен kam2@demo — владельца заявки: KAM пишет только своё) | 200, статус new → first_contact, version инкрементирован (оптимистическая блокировка) |
| `GET http://lms.crm.local/` | 200, UI LMS-заглушки |
| `GET http://cms.crm.local/` | 200, UI CMS-заглушки |

## Валидация манифестов

`make k8s-validate` — оба overlay (k3d, minikube): **50/50 ресурсов valid** (kubeconform -strict).

## Актуализация 2026-09-29 (после ребрендинга фронтенда)

Образ `crm/frontend:dev` пересобран из ветки редизайна (shadcn/Tailwind v4,
токены ДС Ростелекома gen2), импортирован в кластер (`k3d image import`),
`rollout restart deployment/frontend` — успешно. Smoke через ingress:
`GET http://crm.localhost/` → 200 (новый бандл `assets/index-sEp3pUMf.js`),
`GET /api/v1/health/ready` → 200. Кластер живёт с 16.09 без пересоздания;
рестарты у долгоживущих подов — следствие перезапусков Docker Desktop / сна
ноутбука, после каждого поды поднимались сами. Два пода `stuck-check` в
статусе Error (18h назад) — часовые запуски CronJob, попавшие на перезапуск
backend; последующие запуски Completed.

```
NAME                           READY   STATUS      RESTARTS       AGE
backend-67bddd8959-cq6bh       1/1     Running     17 (17h ago)   9d
backend-67bddd8959-fsjjb       1/1     Running     18 (17h ago)   18h
cms-stub-7f9b79fcfb-nnp9q      1/1     Running     21 (17h ago)   13d
frontend-78ffcf9764-kf7qz      1/1     Running     0              18s
keycloak-7478658447-4cndb      1/1     Running     1 (43h ago)    12d
keydb-79cf89b9cb-f4zcg         1/1     Running     25 (17h ago)   13d
lms-stub-5cc66899c9-bhk2d      1/1     Running     26 (17h ago)   13d
minio-0                        1/1     Running     7 (18h ago)    13d
notification-b9cfbfd5f-6cgn9   1/1     Running     24 (17h ago)   12d
postgres-0                     1/1     Running     1 (43h ago)    13d
purge-deleted-29841330-cn77w   0/1     Completed   0              43h
purge-deleted-29842770-xqjjg   0/1     Completed   0              38h
purge-deleted-29844210-lsn9j   0/1     Completed   0              13h
stuck-check-29835360-2lggw     0/1     Completed   0              6d18h
stuck-check-29844000-6xtkv     0/1     Error       0              18h
stuck-check-29844000-vcf9g     0/1     Error       0              18h
stuck-check-29844780-hjsjc     0/1     Completed   0              5h3m
stuck-check-29845020-cqzsn     0/1     Completed   0              11m
stuck-check-29845080-wr78x     0/1     Completed   0              3m34s
```
