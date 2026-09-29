// Нагрузочный сценарий CRM «Вузы» (ЛЦТ 2026) — docs/design/deployment.md §8.
//
// Профиль моделирует «300+ одновременных пользователей» (решение кейсодержателя,
// PLAN.md §2), а не RPS-пушку: виртуальный пользователь = КАМ с think-time.
// Ступени: разгон 0→300 VU, полка 300 (основное окно измерений), пик 400 («300+»), спад.
//
// Аутентификация — выделенный confidential-клиент Keycloak `crm-loadtest`
// (Direct Access Grants включён ТОЛЬКО у него, боевые клиенты чисты).
// Доступ по токену пользователя kam1@demo — обычная роль kam, никакого обхода RBAC.
//
// Запись (POST /requests/{id}/transitions) выполняется осторожно:
// только по живым UUID из подготовленного GET-списка, только по переходам,
// которые движок сам объявил доступными (GET /requests/{id}/transitions),
// с обязательным комментарием и актуальной version из карточки.
// 409 (stale version / конкурентный переход) — валидный отказ движка, не ошибка стенда.
//
// Запуск: `make load-test` либо docker-вариант — см. deploy/load/README.md.

import http from "k6/http";
import { check, sleep } from "k6";
import { Counter } from "k6/metrics";

const BASE = __ENV.BASE_URL || "http://crm.local";
const KC = __ENV.KC_URL || "http://id.crm.local";
const REALM = __ENV.KC_REALM || "crm";
const CLIENT_ID = "crm-loadtest";
const CLIENT_SECRET = __ENV.LOADTEST_CLIENT_SECRET || "";
const USERNAME = __ENV.LOADTEST_USER || "kam1@demo";
const PASSWORD = __ENV.LOADTEST_USER_PASSWORD || "Demo2026!";

const transitionsOk = new Counter("crm_transitions_ok");
const transitionsRejected = new Counter("crm_transitions_rejected_409");

// SMOKE=1 — быстрый санити (10 VU / 30 с) против живого стека вместо полного профиля
const SMOKE = (__ENV.SMOKE || "") === "1";

export const options = SMOKE
  ? {
      scenarios: {
        smoke: { executor: "constant-vus", vus: 10, duration: "30s" },
      },
      thresholds: {
        http_req_failed: ["rate<0.01"],
        checks: ["rate>0.99"],
      },
    }
  : {
      scenarios: {
        steady: {
          executor: "ramping-vus",
          startVUs: 0,
          stages: [
            { duration: "2m", target: 300 }, // разгон до 300 пользователей
            { duration: "5m", target: 300 }, // полка: основное окно измерений SLA
            { duration: "1m", target: 400 }, // «300+»: проверка запаса прочности
            { duration: "1m", target: 0 },   // спад: HPA должен вернуть реплики к 2
          ],
          gracefulRampDown: "30s",
        },
      },
      thresholds: {
        http_req_failed: ["rate<0.01"],                 // <1% ошибок — SLA кейсодержателя
        http_req_duration: ["p(95)<500", "p(99)<1000"], // мс
        checks: ["rate>0.99"],
      },
    };

function authHeaders(token, extra) {
  return {
    headers: Object.assign({ Authorization: `Bearer ${token}` }, extra || {}),
  };
}

// setup() выполняется один раз: токен + пул живых UUID заявок.
// accessTokenLifespan realm'а — 1800 с > длительности прогона (~9 мин),
// поэтому один токен на весь тест корректен и не искажает латентности.
export function setup() {
  if (!CLIENT_SECRET) {
    throw new Error(
      "LOADTEST_CLIENT_SECRET не задан. Возьмите его из секрета кластера:\n" +
        "  kubectl -n crm get secret crm-keycloak-clients " +
        "-o jsonpath='{.data.LOADTEST_CLIENT_SECRET}' | base64 -d",
    );
  }

  // KC_HOST_HEADER: докер-запуск против compose-стека — токен просят у
  // http://keycloak:8080, но iss должен совпасть с KEYCLOAK_ISSUER backend'а
  // (localhost:8080), поэтому Host переопределяется
  const tokenParams = { tags: { name: "POST /token (keycloak)" } };
  if (__ENV.KC_HOST_HEADER) {
    tokenParams.headers = { Host: __ENV.KC_HOST_HEADER };
  }
  const tokenRes = http.post(
    `${KC}/realms/${REALM}/protocol/openid-connect/token`,
    {
      grant_type: "password",
      client_id: CLIENT_ID,
      client_secret: CLIENT_SECRET,
      username: USERNAME,
      password: PASSWORD,
    },
    tokenParams,
  );
  if (tokenRes.status !== 200) {
    throw new Error(
      `Keycloak не выдал токен (${tokenRes.status}): проверьте hosts для ${KC} ` +
        "и что секрет соответствует импортированному realm",
    );
  }
  const token = tokenRes.json("access_token");

  // Пул живых заявок; сид (make demo) гарантирует >= 50 заявок.
  const listRes = http.get(
    `${BASE}/api/v1/requests?limit=50&sort=-created_at`,
    authHeaders(token, undefined),
  );
  if (listRes.status !== 200) {
    throw new Error(
      `GET /api/v1/requests → ${listRes.status}: стенд не готов (make k8s-up && make demo?)`,
    );
  }
  const ids = listRes.json("items").map((i) => i.id);
  if (ids.length === 0) {
    throw new Error("Пул заявок пуст — прогоните сид: make demo");
  }
  return { token, ids };
}

// Один проход = одна «сессия действий» пользователя-КАМа:
// канбан-доска → реестр → карточка → (для каждого 10-го VU) переход по workflow.
export default function (data) {
  const h = authHeaders(data.token);
  const id = data.ids[(__VU + __ITER) % data.ids.length];

  // 1. Канбан-доска (горячее чтение, cache-aside KeyDB)
  const wf = __ITER % 3 === 0 ? "b2c" : "b2b";
  let r = http.get(`${BASE}/api/v1/requests/board?workflow_type=${wf}`, {
    ...h,
    tags: { name: "GET /requests/board" },
  });
  check(r, { "board 200": (x) => x.status === 200 });
  sleep(1 + Math.random() * 2);

  // 2. Реестры (ротация: заявки / вузы / студенты — у студентов ПДн маскированы)
  const registries = [
    ["GET /requests", `${BASE}/api/v1/requests?limit=25&sort=-created_at`],
    ["GET /universities", `${BASE}/api/v1/universities?limit=25`],
    ["GET /students", `${BASE}/api/v1/students?limit=25`],
  ];
  const [regName, regUrl] = registries[__ITER % registries.length];
  r = http.get(regUrl, { ...h, tags: { name: regName } });
  check(r, { "registry 200": (x) => x.status === 200 });
  sleep(1 + Math.random() * 2);

  // 3. Карточка заявки (+актуальная version для optimistic locking)
  r = http.get(`${BASE}/api/v1/requests/${id}`, {
    ...h,
    tags: { name: "GET /requests/{id}" },
  });
  check(r, { "card 200": (x) => x.status === 200 });
  const card = r.status === 200 ? r.json() : null;

  // 4. Запись: ~10% VU делают переход по workflow — только валидный,
  //    из списка, который отдал сам движок для текущего пользователя.
  if (__VU % 10 === 0 && card) {
    const trRes = http.get(`${BASE}/api/v1/requests/${id}/transitions`, {
      ...h,
      tags: { name: "GET /requests/{id}/transitions" },
    });
    if (trRes.status === 200) {
      const body = trRes.json();
      const items = Array.isArray(body) ? body : body.items || [];
      // предпочитаем forward-переход, чтобы не гонять заявки по возвратам
      const tr = items.find((t) => t.kind === "forward") || items[0];
      if (tr) {
        const res = http.post(
          `${BASE}/api/v1/requests/${id}/transitions`,
          JSON.stringify({
            to_status_id: tr.to_status_id,
            comment: "нагрузочный прогон k6",
            version: card.version,
          }),
          {
            ...authHeaders(data.token, { "Content-Type": "application/json" }),
            tags: { name: "POST /requests/{id}/transitions" },
          },
        );
        // 200 — переход применён; 409 — конкурентный VU успел раньше
        // (stale_version / workflow_invalid_transition) — валидный отказ движка.
        const ok = check(res, {
          "transition 200/409": (x) => x.status === 200 || x.status === 409,
        });
        if (ok && res.status === 200) {
          transitionsOk.add(1);
        } else if (res.status === 409) {
          transitionsRejected.add(1);
        }
      }
    }
  }

  // think time: это пользователь с мышкой, а не пушка
  sleep(5 + Math.random() * 10);
}
