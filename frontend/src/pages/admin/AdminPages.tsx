/**
 * Admin-зона (ux.md §14): каждый экран — в своём файле, здесь только
 * реэкспорт для маршрутизатора. Доступ — только роль admin (guard + меню).
 */

export { AdminApprovalsPage } from './ApprovalsPage';
export { AdminAuditPage } from './AuditPage';
export { AdminDictionariesPage } from './DictionariesPage';
export { AdminFeatureFlagsPage } from './FeatureFlagsPage';
export { AdminIntegrationsPage } from './IntegrationsPage';
export { AdminUsersPage } from './UsersPage';
