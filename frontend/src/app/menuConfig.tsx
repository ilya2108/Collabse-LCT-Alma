import type { ReactNode } from 'react';
import { matchPath } from 'react-router-dom';
import type { PermissionCode } from '@/shared/auth/permissions';

/**
 * Единая точка правды по навигации (ux.md §3.2–§3.3): из этого конфига
 * собираются меню сайдбара, guard'ы маршрутов и хлебные крошки.
 * Видимость — по правам (F2), а не по ролям: своя роль «Аналитик» с одними
 * отчётами видит только «Аналитику». Группа видна, если виден хотя бы один пункт.
 */

export interface NavItem {
  key: string;
  path: string;
  label: string;
  icon?: ReactNode;
  /** Право, без которого пункт скрыт (и маршрут закрыт guard'ом). */
  permission?: PermissionCode;
  /** Достаточно любого из прав. */
  anyOf?: PermissionCode[];
}

export interface NavGroup {
  key: string;
  label: string;
  icon?: ReactNode;
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
    permission: 'reports:read',
  },
  {
    key: 'board',
    path: '/board',
    label: 'Доска заявок',
    permission: 'requests:read',
  },
  {
    key: 'registry',
    label: 'Реестры',
    children: [
      {
        key: 'registry-universities',
        path: '/registry/universities',
        label: 'Вузы',
        permission: 'universities:read',
      },
      {
        key: 'registry-contracts',
        path: '/registry/contracts',
        label: 'Договоры',
        permission: 'contracts:read',
      },
      {
        key: 'registry-products',
        path: '/registry/products',
        label: 'Продукты',
        permission: 'products:read',
      },
      {
        key: 'registry-programs',
        path: '/registry/programs',
        label: 'Программы',
        permission: 'programs:read',
      },
    ],
  },
  {
    key: 'talent-pool',
    path: '/talent-pool',
    label: 'Пул талантов',
    permission: 'students:read',
  },
  {
    key: 'workflow',
    path: '/workflow',
    label: 'Конструктор процессов',
    permission: 'workflow:read',
  },
  {
    key: 'import',
    path: '/import',
    label: 'Импорт',
    permission: 'import:run',
  },
  {
    key: 'notifications',
    path: '/settings/notifications',
    label: 'Нотификации',
    permission: 'notifications:self',
  },
  {
    key: 'admin',
    label: 'Администрирование',
    children: [
      {
        key: 'admin-users',
        path: '/admin/users',
        label: 'Пользователи',
        permission: 'admin:users',
      },
      {
        key: 'admin-roles',
        path: '/admin/roles',
        label: 'Роли и права',
        permission: 'admin:roles',
      },
      {
        key: 'admin-dictionaries',
        path: '/admin/dictionaries',
        label: 'Справочники',
        permission: 'admin:dictionaries',
      },
      {
        key: 'admin-feature-flags',
        path: '/admin/feature-flags',
        label: 'Фичефлаги',
        permission: 'admin:flags',
      },
      {
        key: 'admin-approvals',
        path: '/admin/approvals',
        label: 'Согласования',
        permission: 'workflow:approve',
      },
      {
        key: 'admin-integrations',
        path: '/admin/integrations',
        label: 'Интеграции',
        permission: 'admin:audit',
      },
      {
        key: 'admin-audit',
        path: '/admin/audit',
        label: 'Аудит',
        permission: 'admin:audit',
      },
    ],
  },
];

/** Проверка права пользователя — `hasPermission` из AuthContext. */
export type CanFn = (code: PermissionCode) => boolean;

/** Доступен ли пункт: право (если задано) и хотя бы одно из `anyOf` (если задано). */
export function canSeeNavItem(item: NavItem, can: CanFn): boolean {
  const permissionOk = !item.permission || can(item.permission);
  const anyOk = !item.anyOf || item.anyOf.length === 0 || item.anyOf.some((code) => can(code));
  return permissionOk && anyOk;
}

/** Меню сайдбара, отфильтрованное по правам пользователя; пустые группы скрыты. */
export function buildMenuEntries(can: CanFn): NavEntry[] {
  return menuConfig
    .map((entry) =>
      isNavGroup(entry)
        ? { ...entry, children: entry.children.filter((child) => canSeeNavItem(child, can)) }
        : entry,
    )
    .filter((entry) => (isNavGroup(entry) ? entry.children.length > 0 : canSeeNavItem(entry, can)));
}

/** Плоский список доступных пунктов в порядке меню (стартовая страница, вход в «Администрирование»). */
export function accessibleNavItems(can: CanFn, groupKey?: string): NavItem[] {
  return buildMenuEntries(can).flatMap((entry) => {
    if (isNavGroup(entry)) return !groupKey || entry.key === groupKey ? entry.children : [];
    return groupKey ? [] : [entry];
  });
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
    pattern: '/admin/roles/new',
    crumbs: [
      { title: 'Администрирование' },
      { title: 'Роли и права', href: '/admin/roles' },
      { title: 'Новая роль' },
    ],
  },
  {
    // последняя крошка подменяется названием роли в HeaderBar (из кэша карточки роли)
    pattern: '/admin/roles/:roleId',
    crumbs: [
      { title: 'Администрирование' },
      { title: 'Роли и права', href: '/admin/roles' },
      { title: 'Роль' },
    ],
  },
  {
    pattern: '/admin/roles',
    crumbs: [{ title: 'Администрирование' }, { title: 'Роли и права' }],
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
