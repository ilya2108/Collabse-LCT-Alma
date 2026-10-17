import type {
  AdminRole,
  PermissionDef,
  PermissionGroup,
  RecordScope,
  WorkflowType,
} from '@/shared/api/types';

/**
 * Модель редактора ролей (F2 §5): зависимости прав (`requires` каталога),
 * транслит кода роли, состояние формы и его сравнение с сохранённым.
 * Чистые функции — без React, их же использует матрица сравнения ролей.
 */

/** Шаблон кода роли — как CHECK `ck_app_role_code` в БД. */
export const ROLE_CODE_RE = /^[a-z][a-z0-9_]{1,39}$/;
export const ROLE_NAME_MAX = 60;
export const ROLE_DESCRIPTION_MAX = 500;
/** Ключи кэша экранов ролей (их же инвалидирует AccessChangeListener). */
export const ADMIN_ROLES_QUERY_KEY = ['admin-roles'] as const;
export const PERMISSIONS_QUERY_KEY = ['admin-permissions'] as const;

/** Подпись «N из M», пока каталог прав не загружен (60 прав в каталоге F2). */
export const PERMISSIONS_TOTAL_FALLBACK = 60;

/** Зарезервированные коды: сервисные роли Keycloak и встроенные роли realm. */
const RESERVED_CODES = new Set(['offline_access', 'uma_authorization']);

export function roleCodeError(code: string): string | null {
  if (!code) return 'Укажите код роли';
  if (code.startsWith('svc_') || RESERVED_CODES.has(code)) {
    return 'Код зарезервирован за служебными ролями — выберите другой';
  }
  if (!ROLE_CODE_RE.test(code)) {
    return 'Латиница строчными, цифры и «_»: 2–40 символов, первая — буква';
  }
  return null;
}

const TRANSLIT: Record<string, string> = {
  а: 'a',
  б: 'b',
  в: 'v',
  г: 'g',
  д: 'd',
  е: 'e',
  ё: 'e',
  ж: 'zh',
  з: 'z',
  и: 'i',
  й: 'y',
  к: 'k',
  л: 'l',
  м: 'm',
  н: 'n',
  о: 'o',
  п: 'p',
  р: 'r',
  с: 's',
  т: 't',
  у: 'u',
  ф: 'f',
  х: 'kh',
  ц: 'ts',
  ч: 'ch',
  ш: 'sh',
  щ: 'sch',
  ъ: '',
  ы: 'y',
  ь: '',
  э: 'e',
  ю: 'yu',
  я: 'ya',
};

/** Код роли из названия: «Аналитик (заказчик)» → `analitik_zakazchik`. */
export function slugifyRoleCode(name: string): string {
  const latin = name
    .toLowerCase()
    .split('')
    .map((char) => TRANSLIT[char] ?? char)
    .join('');
  let code = latin
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .replace(/_+/g, '_');
  // первая — буква: «1с_отчёты» → «r_1s_otchety»
  if (code && !/^[a-z]/.test(code)) code = `r_${code}`;
  return code.slice(0, 40).replace(/_+$/, '');
}

// --- Зависимости прав ------------------------------------------------------------

export type PermissionIndex = Map<string, PermissionDef>;

export function indexPermissions(groups: PermissionGroup[]): PermissionIndex {
  return new Map(groups.flatMap((group) => group.permissions.map((p) => [p.code, p] as const)));
}

/** Все права, от которых зависит `code` (транзитивно, без самого права). */
export function requirementsOf(code: string, index: PermissionIndex): string[] {
  const result: string[] = [];
  const seen = new Set<string>([code]);
  const queue = [...(index.get(code)?.requires ?? [])];
  while (queue.length > 0) {
    const next = queue.shift() as string;
    if (seen.has(next)) continue;
    seen.add(next);
    result.push(next);
    queue.push(...(index.get(next)?.requires ?? []));
  }
  return result;
}

/** Все права, которые зависят от `code` (транзитивно, без самого права). */
export function dependentsOf(code: string, index: PermissionIndex): string[] {
  const result: string[] = [];
  const seen = new Set<string>([code]);
  const queue = [code];
  while (queue.length > 0) {
    const current = queue.shift() as string;
    for (const def of index.values()) {
      if (!seen.has(def.code) && def.requires.includes(current)) {
        seen.add(def.code);
        result.push(def.code);
        queue.push(def.code);
      }
    }
  }
  return result;
}

export interface PermissionChange {
  next: Set<string>;
  /** Права, добавленные/снятые автоматически (зависимости). */
  cascade: string[];
}

/** Включить права с их зависимостями. */
export function addPermissions(
  selected: ReadonlySet<string>,
  codes: readonly string[],
  index: PermissionIndex,
): PermissionChange {
  const next = new Set(selected);
  const cascade: string[] = [];
  for (const code of codes) {
    next.add(code);
    for (const required of requirementsOf(code, index)) {
      if (!next.has(required) && !codes.includes(required)) cascade.push(required);
      next.add(required);
    }
  }
  return { next, cascade: [...new Set(cascade)] };
}

/** Снять права вместе со всеми зависящими от них. */
export function removePermissions(
  selected: ReadonlySet<string>,
  codes: readonly string[],
  index: PermissionIndex,
): PermissionChange {
  const next = new Set(selected);
  const cascade: string[] = [];
  for (const code of codes) {
    next.delete(code);
    for (const dependent of dependentsOf(code, index)) {
      if (next.has(dependent) && !codes.includes(dependent)) cascade.push(dependent);
      next.delete(dependent);
    }
  }
  return { next, cascade: [...new Set(cascade)] };
}

/** Права набора, у которых не хватает зависимостей: код → недостающие. */
export function missingRequirements(
  selected: ReadonlySet<string>,
  index: PermissionIndex,
): Map<string, string[]> {
  const result = new Map<string, string[]>();
  for (const code of selected) {
    const missing = (index.get(code)?.requires ?? []).filter((r) => !selected.has(r));
    if (missing.length > 0) result.set(code, missing);
  }
  return result;
}

/** «Просмотр заявок», «Комментарии…» — названия прав в кавычках через запятую. */
export function quotedNames(codes: readonly string[], index: PermissionIndex): string {
  return codes.map((code) => `«${index.get(code)?.name ?? code}»`).join(', ');
}

// --- Состояние формы ----------------------------------------------------------------

export interface RoleFormState {
  name: string;
  code: string;
  description: string;
  permissions: Set<string>;
  workflowTypes: WorkflowType[];
  recordScope: RecordScope;
}

export function emptyRoleForm(): RoleFormState {
  return {
    name: '',
    code: '',
    description: '',
    permissions: new Set(),
    workflowTypes: ['b2b', 'b2c'],
    recordScope: 'all',
  };
}

export function formFromRole(role: AdminRole): RoleFormState {
  return {
    name: role.name,
    code: role.code,
    description: role.description ?? '',
    permissions: new Set(role.permissions),
    workflowTypes: [...role.workflow_types],
    recordScope: role.record_scope,
  };
}

/** Копия роли для «Создать на основе»: права и область — те же, название и код — новые. */
export function formFromTemplate(role: AdminRole): RoleFormState {
  const name = `${role.name} (копия)`.slice(0, ROLE_NAME_MAX);
  return { ...formFromRole(role), name, code: slugifyRoleCode(name) };
}

function sameSet(a: ReadonlySet<string>, b: ReadonlySet<string>): boolean {
  if (a.size !== b.size) return false;
  for (const item of a) if (!b.has(item)) return false;
  return true;
}

export function sameWorkflowTypes(a: readonly WorkflowType[], b: readonly WorkflowType[]): boolean {
  return sameSet(new Set(a), new Set(b));
}

export function samePermissions(a: ReadonlySet<string>, b: ReadonlySet<string>): boolean {
  return sameSet(a, b);
}

export function isFormDirty(form: RoleFormState, base: RoleFormState): boolean {
  return (
    form.name.trim() !== base.name.trim() ||
    form.code !== base.code ||
    form.description.trim() !== base.description.trim() ||
    form.recordScope !== base.recordScope ||
    !sameWorkflowTypes(form.workflowTypes, base.workflowTypes) ||
    !samePermissions(form.permissions, base.permissions)
  );
}

/** Права в порядке каталога (стабильный порядок в теле запроса и аудите). */
export function orderedPermissions(
  selected: ReadonlySet<string>,
  groups: PermissionGroup[],
): string[] {
  const ordered = groups.flatMap((group) => group.permissions.map((p) => p.code));
  const known = ordered.filter((code) => selected.has(code));
  const unknown = [...selected].filter((code) => !ordered.includes(code)).sort();
  return [...known, ...unknown];
}

export interface RoleFormErrors {
  name?: string;
  code?: string;
  description?: string;
  workflowTypes?: string;
  permissions?: string;
}

export function validateRoleForm(
  form: RoleFormState,
  { creating }: { creating: boolean },
): RoleFormErrors {
  const errors: RoleFormErrors = {};
  const name = form.name.trim();
  if (name.length < 2) errors.name = 'Название — от 2 символов';
  else if (name.length > ROLE_NAME_MAX) errors.name = `Не более ${ROLE_NAME_MAX} символов`;
  if (creating) {
    const codeError = roleCodeError(form.code);
    if (codeError) errors.code = codeError;
  }
  if (form.description.trim().length > ROLE_DESCRIPTION_MAX) {
    errors.description = `Не более ${ROLE_DESCRIPTION_MAX} символов`;
  }
  if (form.workflowTypes.length === 0) errors.workflowTypes = 'Выберите хотя бы один процесс';
  return errors;
}

export function hasErrors(errors: RoleFormErrors): boolean {
  return Object.values(errors).some(Boolean);
}
