import type { WorkflowType } from '@/shared/api/types';

/**
 * Каталог прав на фронте (F2, Р-F2-3): коды — зеркало `backend/app/core/rbac_catalog.py`.
 * Источник истины для отрисовки — `/auth/me` (`permissions`, `access.by_workflow`);
 * константы здесь нужны для типизации проверок (опечатка в коде права — ошибка
 * компиляции) и для запасного поведения, если сервер не прислал блок `access`.
 * Подписи и описания прав для UI приходят с сервера (`GET /admin/permissions`).
 */

export const P = {
  // Заявки (действуют в процессах роли)
  REQUESTS_READ: 'requests:read',
  REQUESTS_WRITE: 'requests:write',
  REQUESTS_WRITE_ANY: 'requests:write_any',
  REQUESTS_TRANSITION: 'requests:transition',
  REQUESTS_ASSIGN: 'requests:assign',
  REQUESTS_ASSIGN_ANY: 'requests:assign_any',
  REQUESTS_KAM: 'requests:kam',
  REQUESTS_REOPEN: 'requests:reopen',
  REQUESTS_DELETE: 'requests:delete',
  REQUESTS_FILES: 'requests:files',
  COMMENTS_WRITE: 'comments:write',
  COMMENTS_DELETE_ANY: 'comments:delete_any',
  CLIENTS_PII: 'clients:pii',
  // Конструктор процессов (действуют в процессах роли)
  WORKFLOW_READ: 'workflow:read',
  WORKFLOW_HISTORY: 'workflow:history',
  WORKFLOW_CHANGE: 'workflow:change',
  WORKFLOW_APPROVE: 'workflow:approve',
  // Вузы, договоры, продукты, программы
  UNIVERSITIES_READ: 'universities:read',
  UNIVERSITIES_WRITE: 'universities:write',
  UNIVERSITIES_ASSIGN: 'universities:assign',
  UNIVERSITIES_DELETE: 'universities:delete',
  CONTACTS_DELETE: 'contacts:delete',
  CONTRACTS_READ: 'contracts:read',
  CONTRACTS_WRITE: 'contracts:write',
  CONTRACTS_DELETE: 'contracts:delete',
  CONTRACTS_FILES_DELETE: 'contracts:files_delete',
  CRM_WRITE_ANY: 'crm:write_any',
  PRODUCTS_READ: 'products:read',
  PRODUCTS_WRITE: 'products:write',
  PRODUCTS_DELETE: 'products:delete',
  PROGRAMS_READ: 'programs:read',
  PROGRAMS_WRITE: 'programs:write',
  PROGRAMS_PRIORITY: 'programs:priority',
  PROGRAMS_DELETE: 'programs:delete',
  FILES_BUSINESS: 'files:business',
  // Пул талантов и активности
  STUDENTS_READ: 'students:read',
  STUDENTS_WRITE: 'students:write',
  STUDENTS_REVEAL: 'students:reveal',
  STUDENTS_FILES: 'students:files',
  STUDENTS_FILES_DELETE: 'students:files_delete',
  STUDENTS_DELETE: 'students:delete',
  ACTIVITIES_READ: 'activities:read',
  ACTIVITIES_WRITE: 'activities:write',
  ACTIVITIES_PARTICIPANTS: 'activities:participants',
  // Аналитика и экспорт
  REPORTS_READ: 'reports:read',
  REPORTS_ALL: 'reports:all',
  EXPORT_RUN: 'export:run',
  // Импорт
  IMPORT_RUN: 'import:run',
  IMPORT_DICTIONARIES: 'import:dictionaries',
  // Уведомления
  NOTIFICATIONS_SELF: 'notifications:self',
  NOTIFICATION_RULES_READ: 'notification_rules:read',
  NOTIFICATION_RULES_WRITE: 'notification_rules:write',
  // Администрирование
  ADMIN_USERS: 'admin:users',
  ADMIN_USERS_WRITE: 'admin:users_write',
  ADMIN_ROLES: 'admin:roles',
  ADMIN_SETTINGS: 'admin:settings',
  ADMIN_DICTIONARIES: 'admin:dictionaries',
  ADMIN_FLAGS: 'admin:flags',
  ADMIN_AUDIT: 'admin:audit',
  ADMIN_INTEGRATIONS: 'admin:integrations',
  FILES_OTHERS: 'files:others',
} as const;

export type PermissionCode = (typeof P)[keyof typeof P];

export const ALL_PERMISSIONS: readonly PermissionCode[] = Object.values(P);

/** Процессы в порядке UI. */
export const WORKFLOW_TYPES: readonly WorkflowType[] = ['b2b', 'b2c'];

export const WORKFLOW_TYPE_LABELS: Record<WorkflowType, string> = {
  b2b: 'B2B',
  b2c: 'B2C',
};

/** Подписи процессов с пояснением — флажки области роли. */
export const WORKFLOW_TYPE_TITLES: Record<WorkflowType, string> = {
  b2b: 'B2B (вузы)',
  b2c: 'B2C (физлица и юрлица)',
};

/**
 * Права с областью (колонка S каталога): действуют только в процессах роли.
 * Запасной список — сервер присылает актуальный в `access.scoped_permissions`.
 */
export const SCOPED_PERMISSIONS: ReadonlySet<string> = new Set<string>([
  P.REQUESTS_READ,
  P.REQUESTS_WRITE,
  P.REQUESTS_WRITE_ANY,
  P.REQUESTS_TRANSITION,
  P.REQUESTS_ASSIGN,
  P.REQUESTS_ASSIGN_ANY,
  P.REQUESTS_KAM,
  P.REQUESTS_REOPEN,
  P.REQUESTS_DELETE,
  P.REQUESTS_FILES,
  P.COMMENTS_WRITE,
  P.COMMENTS_DELETE_ANY,
  P.WORKFLOW_READ,
  P.WORKFLOW_HISTORY,
  P.WORKFLOW_CHANGE,
  P.WORKFLOW_APPROVE,
  P.REPORTS_READ,
  P.REPORTS_ALL,
]);

/**
 * Права, меняющие данные. Нет ни одного — пользователь работает в режиме
 * просмотра (тег «Режим просмотра» в шапке). Скачивание, экспорт, личные
 * уведомления и раскрытие ПДн изменением данных не считаются.
 */
export const WRITE_PERMISSIONS: readonly PermissionCode[] = [
  P.REQUESTS_WRITE,
  P.REQUESTS_WRITE_ANY,
  P.REQUESTS_TRANSITION,
  P.REQUESTS_ASSIGN,
  P.REQUESTS_ASSIGN_ANY,
  P.REQUESTS_REOPEN,
  P.REQUESTS_DELETE,
  P.REQUESTS_FILES,
  P.COMMENTS_WRITE,
  P.COMMENTS_DELETE_ANY,
  P.WORKFLOW_CHANGE,
  P.WORKFLOW_APPROVE,
  P.UNIVERSITIES_WRITE,
  P.UNIVERSITIES_ASSIGN,
  P.UNIVERSITIES_DELETE,
  P.CONTACTS_DELETE,
  P.CONTRACTS_WRITE,
  P.CONTRACTS_DELETE,
  P.CONTRACTS_FILES_DELETE,
  P.CRM_WRITE_ANY,
  P.PRODUCTS_WRITE,
  P.PRODUCTS_DELETE,
  P.PROGRAMS_WRITE,
  P.PROGRAMS_PRIORITY,
  P.PROGRAMS_DELETE,
  P.STUDENTS_WRITE,
  P.STUDENTS_FILES_DELETE,
  P.STUDENTS_DELETE,
  P.ACTIVITIES_WRITE,
  P.ACTIVITIES_PARTICIPANTS,
  P.IMPORT_RUN,
  P.IMPORT_DICTIONARIES,
  P.NOTIFICATION_RULES_WRITE,
  P.ADMIN_USERS_WRITE,
  P.ADMIN_ROLES,
  P.ADMIN_SETTINGS,
  P.ADMIN_DICTIONARIES,
  P.ADMIN_FLAGS,
  P.ADMIN_INTEGRATIONS,
];

export type PermissionCheck = (code: PermissionCode) => boolean;

/**
 * Профиль интерфейса — какой набор онбординга и подсказок показывать.
 * Выводится из прав, а не из кода роли: своя роль «КАМ B2C» получает профиль kam.
 */
export type AccessProfile = 'admin' | 'head_kam' | 'kam' | 'observer';

export function deriveProfile(can: PermissionCheck): AccessProfile {
  if (can(P.ADMIN_ROLES)) return 'admin';
  if (can(P.WORKFLOW_CHANGE) || can(P.REQUESTS_ASSIGN_ANY)) return 'head_kam';
  if (can(P.REQUESTS_WRITE)) return 'kam';
  return 'observer';
}

export function isReadOnly(can: PermissionCheck): boolean {
  return !WRITE_PERMISSIONS.some((code) => can(code));
}

// --- Уровни конструктора процессов (Р-F2-7) ----------------------------------------

/** Уровень доступа к конструктору процесса: права workflow:* с областью по процессу. */
export type WorkflowLevel = 'none' | 'read' | 'change' | 'approve';

export const WORKFLOW_LEVELS: readonly WorkflowLevel[] = ['none', 'read', 'change', 'approve'];

export const WORKFLOW_LEVEL_LABELS: Record<WorkflowLevel, string> = {
  none: 'Нет доступа',
  read: 'Просмотр',
  change: 'Правка с согласованием',
  approve: 'Правка и публикация',
};

export const WORKFLOW_LEVEL_HINTS: Record<WorkflowLevel, string> = {
  none: 'Конструктор процессов скрыт из меню',
  read: 'Схема процесса без правок',
  change: 'Черновик и пакет изменений — публикует согласующий',
  approve: 'Публикует изменения сам и согласует пакеты других',
};

/** Набор прав workflow:* для уровня; `history` — флажок «История изменений». */
export function workflowLevelPermissions(level: WorkflowLevel, history: boolean): PermissionCode[] {
  switch (level) {
    case 'none':
      return [];
    case 'read':
      return history ? [P.WORKFLOW_READ, P.WORKFLOW_HISTORY] : [P.WORKFLOW_READ];
    case 'change':
      return [P.WORKFLOW_READ, P.WORKFLOW_HISTORY, P.WORKFLOW_CHANGE];
    case 'approve':
      return [P.WORKFLOW_READ, P.WORKFLOW_HISTORY, P.WORKFLOW_CHANGE, P.WORKFLOW_APPROVE];
  }
}

/** Уровень по набору прав (старшее право определяет уровень). */
export function workflowLevelOf(has: (code: PermissionCode) => boolean): WorkflowLevel {
  if (has(P.WORKFLOW_APPROVE)) return 'approve';
  if (has(P.WORKFLOW_CHANGE)) return 'change';
  if (has(P.WORKFLOW_READ)) return 'read';
  return 'none';
}

export const WORKFLOW_PERMISSION_CODES: ReadonlySet<string> = new Set<string>([
  P.WORKFLOW_READ,
  P.WORKFLOW_HISTORY,
  P.WORKFLOW_CHANGE,
  P.WORKFLOW_APPROVE,
]);
