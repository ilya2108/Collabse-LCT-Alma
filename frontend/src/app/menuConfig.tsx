import type { ReactNode } from 'react';
import { matchPath } from 'react-router-dom';
import { ADMIN_ONLY, ALL_ROLES, IMPORT_ROLES, hasAnyRole, type AppRole } from '@/shared/auth/roles';

/**
 * Единая точка правды по навигации (ux.md §3.2–§3.3): из этого конфига
 * собираются меню сайдбара, guard'ы маршрутов и хлебные крошки.
 * Роли — из таблицы маршрутов ux.md; observer видит всё в read-only,
 * кроме импорта (пункт скрыт) и admin-зоны.
 */

export interface NavItem {
  key: string;
  path: string;
  label: string;
  icon?: ReactNode;
  roles: AppRole[];
}

export interface NavGroup {
  key: string;
  label: string;
  icon?: ReactNode;
  roles: AppRole[];
  children: NavItem[];
}

export type NavEntry = NavItem | NavGroup;

export function isNavGroup(entry: NavEntry): entry is NavGroup {
  return 'children' in entry;
}

export const menuConfig: NavEntry[] = [
  {
    key: 'dashboard',
    path: '/dashboard',
    label: 'Аналитика',
    roles: ALL_ROLES,
  },
  {
    key: 'board',
    path: '/board',
    label: 'Доска заявок',
    roles: ALL_ROLES,
  },
  {
    key: 'registry',
    label: 'Реестры',
    roles: ALL_ROLES,
    children: [
      {
        key: 'registry-universities',
        path: '/registry/universities',
        label: 'Вузы',
        roles: ALL_ROLES,
      },
      {
        key: 'registry-contracts',
        path: '/registry/contracts',
        label: 'Договоры',
        roles: ALL_ROLES,
      },
      {
        key: 'registry-products',
        path: '/registry/products',
        label: 'Продукты',
        roles: ALL_ROLES,
      },
      {
        key: 'registry-programs',
        path: '/registry/programs',
        label: 'Программы',
        roles: ALL_ROLES,
      },
    ],
  },
  {
    key: 'talent-pool',
    path: '/talent-pool',
    label: 'Пул талантов',
    roles: ALL_ROLES,
  },
  {
    key: 'workflow',
    path: '/workflow',
    label: 'Конструктор процессов',
    roles: ALL_ROLES,
  },
  {
    key: 'import',
    path: '/import',
    label: 'Импорт',
    roles: IMPORT_ROLES,
  },
  {
    key: 'notifications',
    path: '/settings/notifications',
    label: 'Нотификации',
    roles: ALL_ROLES,
  },
  {
    key: 'admin',
    label: 'Администрирование',
    roles: ADMIN_ONLY,
    children: [
      {
        key: 'admin-users',
        path: '/admin/users',
        label: 'Пользователи',
        roles: ADMIN_ONLY,
      },
      {
        key: 'admin-dictionaries',
        path: '/admin/dictionaries',
        label: 'Справочники',
        roles: ADMIN_ONLY,
      },
      {
        key: 'admin-feature-flags',
        path: '/admin/feature-flags',
        label: 'Фичефлаги',
        roles: ADMIN_ONLY,
      },
      {
        key: 'admin-approvals',
        path: '/admin/approvals',
        label: 'Согласования',
        roles: ADMIN_ONLY,
      },
      {
        key: 'admin-integrations',
        path: '/admin/integrations',
        label: 'Интеграции',
        roles: ADMIN_ONLY,
      },
      {
        key: 'admin-audit',
        path: '/admin/audit',
        label: 'Аудит',
        roles: ADMIN_ONLY,
      },
    ],
  },
];

/** Меню сайдбара, отфильтрованное по ролям пользователя. */
export function buildMenuEntries(roles: readonly AppRole[]): NavEntry[] {
  return menuConfig
    .filter((entry) => hasAnyRole(roles, entry.roles))
    .map((entry) =>
      isNavGroup(entry)
        ? { ...entry, children: entry.children.filter((child) => hasAnyRole(roles, child.roles)) }
        : entry,
    )
    .filter((entry) => !isNavGroup(entry) || entry.children.length > 0);
}

/** Ключ выбранного пункта меню + открытая группа для текущего pathname. */
export function activeMenuKeys(pathname: string): { selected: string[]; open: string[] } {
  for (const entry of menuConfig) {
    if (isNavGroup(entry)) {
      for (const child of entry.children) {
        if (matchPath({ path: `${child.path}/*` }, pathname) || matchPath(child.path, pathname)) {
          return { selected: [child.key], open: [entry.key] };
        }
      }
    } else if (matchPath({ path: `${entry.path}/*` }, pathname) || matchPath(entry.path, pathname)) {
      return { selected: [entry.key], open: [] };
    }
  }
  return { selected: [], open: [] };
}

export interface Crumb {
  title: string;
  /** Путь для ссылки; последняя крошка ссылки не имеет. */
  href?: string;
}

interface BreadcrumbRule {
  pattern: string;
  crumbs: Crumb[];
}

/** Правила хлебных крошек: первый матч побеждает (порядок — от частного к общему). */
const BREADCRUMB_RULES: BreadcrumbRule[] = [
  { pattern: '/dashboard', crumbs: [{ title: 'Аналитика' }] },
  {
    pattern: '/board',
    crumbs: [{ title: 'Доска заявок' }],
  },
  {
    pattern: '/requests/:id',
    crumbs: [{ title: 'Доска заявок', href: '/board' }, { title: 'Карточка заявки' }],
  },
  {
    pattern: '/registry/universities/:id',
    crumbs: [
      { title: 'Реестры' },
      { title: 'Вузы', href: '/registry/universities' },
      { title: 'Карточка вуза' },
    ],
  },
  {
    pattern: '/registry/universities',
    crumbs: [{ title: 'Реестры' }, { title: 'Вузы' }],
  },
  {
    pattern: '/registry/contracts/:id',
    crumbs: [
      { title: 'Реестры' },
      { title: 'Договоры', href: '/registry/contracts' },
      { title: 'Карточка договора' },
    ],
  },
  {
    pattern: '/registry/contracts',
    crumbs: [{ title: 'Реестры' }, { title: 'Договоры' }],
  },
  {
    pattern: '/registry/products/:id',
    crumbs: [
      { title: 'Реестры' },
      { title: 'Продукты', href: '/registry/products' },
      { title: 'Карточка продукта' },
    ],
  },
  {
    pattern: '/registry/products',
    crumbs: [{ title: 'Реестры' }, { title: 'Продукты' }],
  },
  {
    pattern: '/registry/programs/:id',
    crumbs: [
      { title: 'Реестры' },
      { title: 'Программы', href: '/registry/programs' },
      { title: 'Карточка программы' },
    ],
  },
  {
    pattern: '/registry/programs',
    crumbs: [{ title: 'Реестры' }, { title: 'Программы' }],
  },
  {
    pattern: '/talent-pool/students/:id',
    crumbs: [{ title: 'Пул талантов', href: '/talent-pool' }, { title: 'Карточка студента' }],
  },
  { pattern: '/talent-pool', crumbs: [{ title: 'Пул талантов' }] },
  { pattern: '/workflow', crumbs: [{ title: 'Конструктор процессов' }] },
  { pattern: '/import', crumbs: [{ title: 'Мастер импорта' }] },
  {
    pattern: '/settings/notifications',
    crumbs: [{ title: 'Настройки' }, { title: 'Нотификации' }],
  },
  {
    pattern: '/admin/users',
    crumbs: [{ title: 'Администрирование' }, { title: 'Пользователи' }],
  },
  {
    pattern: '/admin/dictionaries',
    crumbs: [{ title: 'Администрирование' }, { title: 'Справочники' }],
  },
  {
    pattern: '/admin/feature-flags',
    crumbs: [{ title: 'Администрирование' }, { title: 'Фичефлаги' }],
  },
  {
    pattern: '/admin/approvals',
    crumbs: [{ title: 'Администрирование' }, { title: 'Согласования процессов' }],
  },
  {
    pattern: '/admin/integrations',
    crumbs: [{ title: 'Администрирование' }, { title: 'Интеграции LMS/CMS' }],
  },
  {
    pattern: '/admin/audit',
    crumbs: [{ title: 'Администрирование' }, { title: 'Журнал аудита' }],
  },
];

export function breadcrumbsFor(pathname: string): Crumb[] {
  for (const rule of BREADCRUMB_RULES) {
    if (matchPath(rule.pattern, pathname)) return rule.crumbs;
  }
  return [];
}
