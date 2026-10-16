/**
 * Admin-зона (ux.md §14): каждый экран — в своём файле, здесь только
 * реэкспорт для маршрутизатора. Доступ — по правам `admin:*` (guard + меню, F2).
 */

export { AdminApprovalsPage } from './ApprovalsPage';
export { AdminAuditPage } from './AuditPage';
export { AdminDictionariesPage } from './DictionariesPage';
export { AdminFeatureFlagsPage } from './FeatureFlagsPage';
export { AdminIntegrationsPage } from './IntegrationsPage';
export { AdminRoleEditorPage } from './roles/RoleEditorPage';
export { AdminRolesPage } from './roles/RolesPage';
export { AdminUsersPage } from './UsersPage';
