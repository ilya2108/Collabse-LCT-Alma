# Frontend «Альмы»

SPA-клиент CRM для взаимодействия с вузами (ЛЦТ 2026). Реализует UX-блюпринт
`docs/design/ux.md` поверх REST-контракта `docs/design/api-contract.md`.

## Стек

- **React 18 + TypeScript + Vite** — статический бандл под nginx, без CDN и
  внешних ресурсов (закрытый контур: шрифт Inter и все ассеты в бандле).
- **Ant Design 5** (`ru_RU`), тема — «сдержанный корпоративный синий»
  (`src/shared/config/theme.ts`); тёмной темы нет (решение кейсодержателя).
- **@tanstack/react-query** — серверное состояние, кэш, инвалидация по SSE.
- **@xyflow/react** — конструктор workflow (ленивый чанк).
- **Apache ECharts** — дашборд (ленивый чанк), экспорт PNG через `getDataURL`.
- **keycloak-js** — OIDC + PKCE, realm `crm`, клиент `crm-frontend`.
- **@dnd-kit** — drag&drop канбана заявок и воронки студентов.
- **zustand** — черновик конструктора workflow (persist в localStorage).
- **qrcode** — QR для привязки Telegram-бота (генерация на клиенте).

## Команды

```bash
npm install
npm run dev        # http://localhost:5173, прокси /api → http://localhost:8000
npm run typecheck  # tsc --noEmit
npm run build      # tsc + vite build → dist/
npm run preview    # прод-превью dist/ (порт 4173)
```

## Конфигурация окружения

Приоритет (см. `src/shared/config/env.ts`):

1. `window.__ENV__` из `/config.js` — рантайм-конфиг (ConfigMap/entrypoint в
   Docker: `config.js.template`), один образ на все окружения;
2. `VITE_API_URL`, `VITE_KEYCLOAK_URL`, `VITE_KEYCLOAK_REALM`,
   `VITE_KEYCLOAK_CLIENT_ID` — для локальной разработки;
3. дефолты: API `/api` (v1 добавляется клиентом), Keycloak
   `http://localhost:8080`, realm `crm`, клиент `crm-frontend`.

Демо-пользователи (`admin@demo`, `head@demo`, `kam1@demo`, `kam2@demo`,
`observer@demo`) импортируются в Keycloak из JSON-реалма — пароли в корневом
README проекта.

## Структура

```
src/
  app/            каркас: маршрутизатор, guard'ы, layout, конфиг меню
  pages/          экраны
    board/          канбан заявок (drag&drop, возвраты с комментарием)
    dashboard/      ECharts-дашборд (ленивый чанк): KPI, воронки, drill-down
    import/         мастер импорта 4 шага (XLSX/XLS/CSV/JSON, кодировки, маппинг)
    registry/       реестры: вузы, договоры, продукты, программы (ранжирование)
    talent-pool/    воронка студентов, реестр с маскированными ПДн, карточка,
                    сквозное расписание; раскрытие ПДн на 60 с через /reveal
    workflow/       конструктор схем (React Flow, ленивый чанк), красный сценарий
    notifications/  каналы (TG/Email/Max), подписки, пороги, привязка бота (QR)
    admin/          пользователи, справочники, фичефлаги, согласования,
                    монитор интеграций LMS/CMS, журнал аудита
  components/     переиспользуемые блоки (RegistryTable с пресетами и экспортом,
                  карточка заявки, селекты-справочники, колокольчик уведомлений)
  shared/
    api/          fetch-клиент (Bearer + refresh, единый формат ошибок),
                  SSE-клиент (fetch-stream, backoff), endpoints по модулям, типы
    auth/         keycloak-js, AuthContext (roles/permissions из /auth/me), <Can>
    config/       env, тема AntD, семантические цвета статусов
    lib/          dayjs (ru), форматтеры, фичефлаги (SSE flags.updated), count-up
    ui/           состояния экранов «пусто/загрузка/ошибка», PageHeader, теги
```

## Ключевые решения

- **RBAC** — только UX-слой: меню/кнопки собираются из ролей и `permissions`
  из `GET /auth/me`; сервер дублирует все проверки. Observer видит всё
  read-only, ПДн студентов — маскированными и без кнопки раскрытия.
- **ПДн (152-ФЗ)**: маскирование серверное; открытые значения приходят только
  из `POST /students/{id}/reveal` (backend пишет аудит), UI держит их в памяти
  60 секунд с обратным отсчётом и никогда не кладёт в storage/кэш.
- **Realtime**: единый SSE-канал `GET /events/stream` (Authorization-заголовок,
  реконнект с backoff, Last-Event-ID). Топики: `workflow.changed` (доски и
  дашборд перечитываются), `request.*`, `telegram.linked` (модалка привязки
  закрывается сама), `flags.updated` (фичефлаги на лету),
  `notification.created` (колокольчик).
- **Экспорт**: таблицы — `POST /export/jobs` (XLSX/CSV, поллинг 2 с, скачивание
  в blob); дашборд — PNG на клиенте (`getDataURL`, композит всего дашборда на
  canvas) + XLSX/CSV через тот же `/export/jobs` (`report:dashboard`); PDF —
  за фичефлагом `report_pdf` (вне MVP).
- **Черновик конструктора** — клиентский (zustand + localStorage, переживает
  F5); на сервер уходит пакет операций `POST /workflows/{id}/changes` с
  impact-preview; опасные операции (rename/delete) — pending + подтверждение
  имени + approve администратора.
- **Тяжёлые чанки** — ECharts-дашборд и React Flow-конструктор подключены через
  `React.lazy`, Vite режет их в отдельные бандлы; основной чанк грузится без них.

## Адаптивность

До планшета (768px): KPI по 2 в ряд, виджеты дашборда в одну колонку, канбан и
доска студентов — горизонтальный скролл со scroll-snap, таблицы скрывают
вторичные колонки (`responsive`), конструктор workflow на узких экранах —
просмотр с предупреждением. Мобильная версия не требуется по решению
кейсодержателя.
