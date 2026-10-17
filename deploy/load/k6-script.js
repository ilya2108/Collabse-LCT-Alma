// Нагрузочные сценарии CRM «Альма» (ЛЦТ 2026) — docs/design/deployment.md §8,
// методика — deploy/load/README.md, отчёт — docs/evidence/load-report.md.
//
// SCENARIO (env) выбирает профиль:
//   steady  (по умолчанию) — «300+ одновременных пользователей»: разгон 0→300 VU, полка 300,
//           пик 400, спад; VU = КАМ kam1@demo с think-time (профиль прогона 16.09 — для
//           сравнимости результатов);
//   users50 — 50 VU под 5+ разными учётками (LOADTEST_USERS) — НФТ «50 параллельных
//           пользователей»: тот же путь КАМа, у каждого VU — токен своей учётки;
//   reports — 10 VU строят отчёты параллельно — НФТ по одновременным отчётам (от 10, лёгкие
//           и тяжёлые вперемешку): разные коды отчётов и фильтры, холодный кэш (период и фильтры
//           случайны — ключ кэша отчёта каждый раз новый), плюс выгрузки XLSX/PDF;
//   nft     — users50 и reports одновременно: 10 отчётов на фоне 50 пользователей.
//
// Аутентификация — выделенный confidential-клиент Keycloak `crm-loadtest`
// (Direct Access Grants включён ТОЛЬКО у него, боевые клиенты чисты).
// Доступ — токенами обычных демо-пользователей, никакого обхода RBAC.
//
// Запись (POST /requests/{id}/transitions) выполняется осторожно:
// только по живым UUID из подготовленного GET-списка, только по переходам,
// которые движок сам объявил доступными (GET /requests/{id}/transitions),
// с обязательным комментарием и актуальной version из карточки.
// 409 (stale version / конкурентный переход) — валидный отказ движка, не ошибка стенда.
//
// Запуск: `make load-test` (steady), `make load-test-reports`, `make load-test-nft`
// либо docker-вариант — см. deploy/load/README.md.

import http from "k6/http";
import { check, sleep } from "k6";
import { Counter, Trend } from "k6/metrics";

const BASE = __ENV.BASE_URL || "http://crm.local";
const KC = __ENV.KC_URL || "http://id.crm.local";
const REALM = __ENV.KC_REALM || "crm";
const CLIENT_ID = "crm-loadtest";
const CLIENT_SECRET = __ENV.LOADTEST_CLIENT_SECRET || "";
const USERNAME = __ENV.LOADTEST_USER || "kam1@demo";
const PASSWORD = __ENV.LOADTEST_USER_PASSWORD || "Demo2026!";

const SCENARIO = (__ENV.SCENARIO || "steady").toLowerCase();
const DURATION = __ENV.DURATION || "5m";
const USERS_VUS = parseInt(__ENV.USERS_VUS || "50", 10);
const REPORT_VUS = parseInt(__ENV.REPORT_VUS || "10", 10);
const MIN_ACCOUNTS = 5;

function list(value, fallback) {
  return (value || fallback)
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

// учётки realm (deploy/keycloak/realm-crm.json): у всех пароль LOADTEST_USER_PASSWORD
const USERS = list(
  __ENV.LOADTEST_USERS,
  "kam1@demo,kam2@demo,head@demo,admin@demo,observer@demo",
);
// кто строит отчёты: руководитель, админ и наблюдатель видят всех КАМов, КАМы — свою область
const REPORT_USERS = list(
  __ENV.REPORT_USERS,
  "head@demo,admin@demo,observer@demo,kam1@demo,kam2@demo",
);
// отчёты дашборда (GET /reports/{code}) + «Активность КАМов»; EXTRA_REPORTS — новые коды,
// например «interactions», когда они развёрнуты на стенде
const REPORT_CODES = [
  "dashboard",
  "funnel",
  "kam-workload",
  "stuck",
  "universities-coverage",
  "programs-demand",
  "talent-pool-funnel",
  "dynamics",
  "kam-activity",
  "kam-activity/summary",
].concat(list(__ENV.EXTRA_REPORTS, ""));

const transitionsOk = new Counter("crm_transitions_ok");
const transitionsRejected = new Counter("crm_transitions_rejected_409");
const reportMs = new Trend("crm_report_ms", true);
const exportMs = new Trend("crm_export_ms", true);

// SMOKE=1 — быстрый санити (10 VU / 30 с) против живого стека вместо полного профиля
const SMOKE = (__ENV.SMOKE || "") === "1";

const STEADY = {
  executor: "ramping-vus",
  exec: "kamSession",
  startVUs: 0,
  stages: [
    { duration: "2m", target: 300 }, // разгон до 300 пользователей
    { duration: "5m", target: 300 }, // полка: основное окно измерений SLA
    { duration: "1m", target: 400 }, // «300+»: проверка запаса прочности
    { duration: "1m", target: 0 }, // спад: HPA должен вернуть реплики к 2
  ],
  gracefulRampDown: "30s",
};
const USERS50 = {
  executor: "constant-vus",
  exec: "kamSession",
  vus: USERS_VUS,
  duration: DURATION,
  env: { ROTATE_USERS: "1" },
  tags: { profile: "users50" },
};
const REPORTS = {
  executor: "constant-vus",
  exec: "reportSession",
  vus: REPORT_VUS,
  duration: DURATION,
  tags: { profile: "reports" },
};

function scenarios() {
  if (SMOKE) {
    return { smoke: { executor: "constant-vus", exec: "kamSession", vus: 10, duration: "30s" } };
  }
  switch (SCENARIO) {
    case "users50":
      return { users50: USERS50 };
    case "reports":
      return { reports: REPORTS };
    case "nft":
      return { users50: USERS50, reports: REPORTS };
    case "steady":
      return { steady: STEADY };
    default:
      throw new Error(`SCENARIO=${SCENARIO}: ожидается steady | users50 | reports | nft`);
  }
}

// пороги — цели прогона, а не результаты: k6 завершится ненулевым кодом, если их не выдержать
function thresholds() {
  const base = {
    http_req_failed: ["rate<0.01"], // <1% ошибок — SLA кейсодержателя
    checks: ["rate>0.99"],
  };
  if (SMOKE) return base;
  if (SCENARIO === "steady") {
    return Object.assign(base, { http_req_duration: ["p(95)<500", "p(99)<1000"] }); // мс
  }
  const result = base;
  if (SCENARIO !== "reports") {
    // экраны КАМа: доска, реестры, карточка, переход — НФТ «не более 1 секунды»
    result["http_req_duration{kind:ui}"] = ["p(95)<500", "p(99)<1000"];
  }
  if (SCENARIO !== "users50") {
    // построение отчёта со сменой фильтров на холодном кэше — та же секунда ТЗ
    result.crm_report_ms = ["p(95)<1000"];
    // формирование файла выгрузки ТЗ не нормирует — цель команды, а не требование
    result.crm_export_ms = ["p(95)<3000"];
  }
  return result;
}

export const options = { scenarios: scenarios(), thresholds: thresholds() };

function authHeaders(token, extra) {
  return {
    headers: Object.assign({ Authorization: `Bearer ${token}` }, extra || {}),
  };
}

function login(username) {
  // KC_HOST_HEADER: докер-запуск против compose-стека — токен просят у
  // http://keycloak:8080, но iss должен совпасть с KEYCLOAK_ISSUER backend'а
  // (localhost:8080), поэтому Host переопределяется
  const params = { tags: { name: "POST /token (keycloak)" } };
  if (__ENV.KC_HOST_HEADER) {
    params.headers = { Host: __ENV.KC_HOST_HEADER };
  }
  const res = http.post(
    `${KC}/realms/${REALM}/protocol/openid-connect/token`,
    {
      grant_type: "password",
      client_id: CLIENT_ID,
      client_secret: CLIENT_SECRET,
      username,
      password: PASSWORD,
    },
    params,
  );
  if (res.status !== 200) {
    throw new Error(
      `Keycloak не выдал токен для ${username} (${res.status}): проверьте hosts для ${KC}, ` +
        "секрет клиента и пароль демо-учётки",
    );
  }
  return res.json("access_token");
}

function ids(res, pick) {
  if (res.status !== 200) return [];
  return (res.json("items") || []).map(pick).filter(Boolean);
}

// setup() выполняется один раз: токены всех учёток + пул живых UUID заявок и фильтров.
// accessTokenLifespan realm'а — 1800 с: прогон длиннее ~25 минут требует перелогина.
export function setup() {
  if (!CLIENT_SECRET) {
    throw new Error(
      "LOADTEST_CLIENT_SECRET не задан. Возьмите его из секрета кластера:\n" +
        "  kubectl -n crm get secret crm-keycloak-clients " +
        "-o jsonpath='{.data.LOADTEST_CLIENT_SECRET}' | base64 -d",
    );
  }
  const needsMany = !SMOKE && (SCENARIO === "users50" || SCENARIO === "nft");
  if (needsMany && new Set(USERS).size < MIN_ACCOUNTS) {
    throw new Error(`Для ${SCENARIO} нужно не меньше ${MIN_ACCOUNTS} учёток в LOADTEST_USERS`);
  }
  const accounts = Array.from(new Set([USERNAME].concat(USERS, REPORT_USERS)));
  const tokens = {};
  for (const username of accounts) tokens[username] = login(username);
  const token = tokens[USERNAME];

  // Пул живых заявок; сид (make demo / make demo-reset) гарантирует >= 50 заявок.
  const listRes = http.get(
    `${BASE}/api/v1/requests?limit=100&sort=-created_at`,
    authHeaders(token, undefined),
  );
  if (listRes.status !== 200) {
    throw new Error(
      `GET /api/v1/requests → ${listRes.status}: стенд не готов (make k8s-up && make demo?)`,
    );
  }
  const requestIds = ids(listRes, (i) => i.id);
  if (requestIds.length === 0) {
    throw new Error("Пул заявок пуст — прогоните сид: make demo-reset");
  }
  // фильтры отчётов: КАМы из заявок, вузы и продукты из справочников
  const kamIds = Array.from(new Set(ids(listRes, (i) => i.assignee && i.assignee.id)));
  const universityIds = ids(
    http.get(`${BASE}/api/v1/universities?limit=100`, authHeaders(token, undefined)),
    (i) => i.id,
  );
  const productIds = ids(
    http.get(`${BASE}/api/v1/products?limit=50`, authHeaders(token, undefined)),
    (i) => i.id,
  );
  return { token, tokens, ids: requestIds, kamIds, universityIds, productIds };
}

function pick(items, n) {
  return items.length ? items[n % items.length] : null;
}

function rand(min, max) {
  return min + Math.floor(Math.random() * (max - min + 1));
}

// --- Сценарий «пользователь-КАМ» (steady, users50, smoke) --------------------------------
// Один проход = одна «сессия действий»: канбан-доска → реестр → карточка →
// (для каждого 10-го VU) переход по workflow.
export function kamSession(data) {
  const username = __ENV.ROTATE_USERS === "1" ? USERS[(__VU - 1) % USERS.length] : USERNAME;
  const token = data.tokens[username] || data.token;
  const h = authHeaders(token);
  const ui = (name) => ({ ...h, tags: { name, kind: "ui", user: username } });
  const id = data.ids[(__VU + __ITER) % data.ids.length];

  // 1. Канбан-доска (горячее чтение, cache-aside KeyDB)
  const wf = __ITER % 3 === 0 ? "b2c" : "b2b";
  let r = http.get(`${BASE}/api/v1/requests/board?workflow_type=${wf}`, ui("GET /requests/board"));
  check(r, { "board 200": (x) => x.status === 200 });
  sleep(1 + Math.random() * 2);

  // 2. Реестры (ротация: заявки / вузы / студенты — у студентов ПДн маскированы)
  const registries = [
    ["GET /requests", `${BASE}/api/v1/requests?limit=25&sort=-created_at`],
    ["GET /universities", `${BASE}/api/v1/universities?limit=25`],
    ["GET /students", `${BASE}/api/v1/students?limit=25`],
  ];
  const [regName, regUrl] = registries[__ITER % registries.length];
  r = http.get(regUrl, ui(regName));
  check(r, { "registry 200": (x) => x.status === 200 });
  sleep(1 + Math.random() * 2);

  // 3. Карточка заявки (+актуальная version для optimistic locking)
  r = http.get(`${BASE}/api/v1/requests/${id}`, ui("GET /requests/{id}"));
  check(r, { "card 200": (x) => x.status === 200 });
  const card = r.status === 200 ? r.json() : null;

  // 4. Запись: ~10% VU делают переход по workflow — только валидный,
  //    из списка, который отдал сам движок для текущего пользователя.
  if (__VU % 10 === 0 && card) {
    const trRes = http.get(
      `${BASE}/api/v1/requests/${id}/transitions`,
      ui("GET /requests/{id}/transitions"),
    );
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
            ...authHeaders(token, { "Content-Type": "application/json" }),
            tags: { name: "POST /requests/{id}/transitions", kind: "ui", user: username },
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

// default — для запуска без SCENARIO старыми командами (k6 run k6-script.js)
export default kamSession;

// --- Сценарий «отчёты» (reports, nft) ---------------------------------------------------

function isoDay(daysAgo) {
  const d = new Date(Date.now() - daysAgo * 86400000);
  return d.toISOString().slice(0, 10);
}

/** Случайный период и фильтры: ключ кэша отчёта (sha1 параметров) каждый раз новый. */
function randomFilters(data, n) {
  const toAgo = rand(0, 10);
  const filters = { from: isoDay(toAgo + rand(7, 120)), to: isoDay(toAgo) };
  const wf = ["", "b2b", "b2c"][n % 3];
  if (wf) filters.workflow_type = wf;
  const kam = pick(data.kamIds, n + rand(0, 50));
  if (kam && n % 2 === 0) filters.kam_id = kam;
  const university = pick(data.universityIds, n + rand(0, 50));
  if (university && n % 5 === 1 && wf !== "b2c") filters.university_id = university;
  const product = pick(data.productIds, n);
  if (product && n % 4 === 3) filters.product_id = product;
  return filters;
}

function query(filters, code) {
  const parts = [];
  for (const [key, value] of Object.entries(filters)) {
    // «Активность КАМов» не знает продуктов; прочие фильтры у отчётов общие
    if (code.startsWith("kam-activity") && key === "product_id") continue;
    parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(value)}`);
  }
  if (code === "stuck") parts.push(`threshold_days=${rand(7, 30)}`);
  return parts.join("&");
}

function exportJob(data, token, username, n, filters) {
  // выгрузки разной тяжести: многолистовой XLSX и PDF «Активности КАМов», таблица отчёта,
  // реестр заявок; фильтры — скаляры (как их шлёт интерфейс)
  const variants = [
    { entity_type: "report:kam-activity", format: "xlsx" },
    { entity_type: "report:kam-activity", format: "pdf" },
    { entity_type: "report:dashboard", format: "xlsx" },
    { entity_type: "requests", format: "xlsx" },
  ];
  const variant = variants[n % variants.length];
  const body = {
    entity_type: variant.entity_type,
    format: variant.format,
    filters:
      variant.entity_type === "requests"
        ? filters.workflow_type
          ? { workflow_type: filters.workflow_type }
          : {}
        : { from: filters.from, to: filters.to, workflow_type: filters.workflow_type },
  };
  const tags = { name: `POST /export/jobs ${variant.entity_type} ${variant.format}`, kind: "export", user: username };
  const started = Date.now();
  const created = http.post(`${BASE}/api/v1/export/jobs`, JSON.stringify(body), {
    ...authHeaders(token, { "Content-Type": "application/json" }),
    tags,
  });
  const ok = check(created, { "export 201": (x) => x.status === 201 });
  if (!ok) return;
  const jobId = created.json("id");
  const file = http.get(`${BASE}/api/v1/export/jobs/${jobId}/download`, {
    ...authHeaders(token),
    tags: { name: "GET /export/jobs/{id}/download", kind: "export", user: username },
  });
  check(file, { "export download 200": (x) => x.status === 200 && x.body && x.body.length > 0 });
  exportMs.add(Date.now() - started, { entity: variant.entity_type, format: variant.format });
}

export function reportSession(data) {
  const n = __VU * 1000 + __ITER;
  const username = REPORT_USERS[(__VU - 1) % REPORT_USERS.length];
  const token = data.tokens[username];
  // соседние VU строят разные отчёты одновременно — «разной сложности»
  const code = REPORT_CODES[(__VU + __ITER) % REPORT_CODES.length];
  const filters = randomFilters(data, n);
  const res = http.get(`${BASE}/api/v1/reports/${code}?${query(filters, code)}`, {
    ...authHeaders(token),
    tags: { name: `GET /reports/${code}`, kind: "report", user: username },
  });
  check(res, { "report 200": (x) => x.status === 200 });
  reportMs.add(res.timings.duration, { code });

  // каждая третья итерация — выгрузка файла по тем же фильтрам
  if (__ITER % 3 === 2) exportJob(data, token, username, n, filters);

  // аналитик смотрит на результат и меняет фильтр — короткая пауза
  sleep(1 + Math.random() * 2);
}
