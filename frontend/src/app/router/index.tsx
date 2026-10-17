import { useEffect } from 'react';
import type { ReactNode } from 'react';
import { createBrowserRouter, useRouteError } from 'react-router-dom';
import { AppLayout } from '@/app/layout/AppLayout';
import { ADMIN_GUIDE_PERMISSIONS } from '@/app/menuConfig';
import { isApiError } from '@/shared/api/errors';
import { ErrorState } from '@/shared/ui/data-table';
import type { PermissionCode } from '@/shared/auth/permissions';
import {
  AdminApprovalsPage,
  AdminAuditPage,
  AdminDictionariesPage,
  AdminFeatureFlagsPage,
  AdminIntegrationsPage,
  AdminRoleEditorPage,
  AdminRolesPage,
  AdminUsersPage,
} from '@/pages/admin/AdminPages';
import { BoardPage } from '@/pages/board/BoardPage';
import { DashboardPage } from '@/pages/DashboardPage';
import { ForbiddenPage, NotFoundPage } from '@/pages/ErrorPages';
import { AdminHelpPage, HelpPage } from '@/pages/help/HelpPage';
import { ImportPage } from '@/pages/ImportPage';
import { KamActivityPage } from '@/pages/KamActivityPage';
import { LoginPage } from '@/pages/LoginPage';
import { NotificationSettingsPage } from '@/pages/NotificationSettingsPage';
import { ContractDetailPage } from '@/pages/registry/ContractDetailPage';
import { ContractsPage } from '@/pages/registry/ContractsPage';
import { ProductDetailPage } from '@/pages/registry/ProductDetailPage';
import { ProductsPage } from '@/pages/registry/ProductsPage';
import { ProgramDetailPage } from '@/pages/registry/ProgramDetailPage';
import { ProgramsPage } from '@/pages/registry/ProgramsPage';
import { UniversitiesPage } from '@/pages/registry/UniversitiesPage';
import { UniversityDetailPage } from '@/pages/registry/UniversityDetailPage';
import { RequestPage } from '@/pages/RequestPage';
import { StudentPage, TalentPoolPage } from '@/pages/TalentPoolPages';
import { WorkflowPage } from '@/pages/WorkflowPage';
import { OnboardingProvider } from '@/features/onboarding';
import { HomeRedirect, RequireAuth, RequirePermission } from './guards';

/** Русское описание для UI вместо сырого сообщения рантайм-ошибки (Р11, §3.5). */
const RENDER_ERROR_FALLBACK = new Error(
  'Экран не смог отрисоваться. Обновите страницу — если ошибка повторится, обратитесь к администратору.',
);

/**
 * Фолбэк errorElement (находка дизайн-ревью): вместо дефолтного белого
 * «Unexpected Application Error!» React Router — ErrorState §3.5 с иллюстрацией
 * error-broken и кнопкой обновления. `full` — вне layout (упал сам каркас).
 */
function RouteErrorScreen({ full = false }: { full?: boolean }): ReactNode {
  const error = useRouteError();
  useEffect(() => {
    // Реальная причина — в консоль; в UI стектрейс не показываем (§3.5)
    console.error('Ошибка рендера маршрута:', error);
  }, [error]);
  const state = (
    <ErrorState
      // У ApiError сообщение уже русское (+ traceId); прочее — общий текст
      error={isApiError(error) ? error : RENDER_ERROR_FALLBACK}
      title="Что-то пошло не так"
      onRetry={() => window.location.reload()}
    />
  );
  if (full) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background px-6">{state}</div>
    );
  }
  return <div className="app-content">{state}</div>;
}

/** Маршрут за guard'ом права: скрытый пункт меню страхуется редиректом на /403. */
function guarded(permission: PermissionCode, element: ReactNode): ReactNode {
  return <RequirePermission permission={permission}>{element}</RequirePermission>;
}

/**
 * Маршрутизация (таблица маршрутов — ux.md §3.2). Права guard'ов совпадают
 * с menuConfig (F2): скрытый пункт меню страхуется редиректом на /403.
 * errorElement: на корне — полноэкранный (упал layout/guard), на pathless-обёртке
 * детей — внутри AppLayout (сайдбар и шапка сохраняются).
 */
export const router = createBrowserRouter([
  { path: '/login', element: <LoginPage />, errorElement: <RouteErrorScreen full /> },
  {
    path: '/',
    element: (
      <RequireAuth>
        {/* Онбординг §7: провайдер внутри Router вокруг layout (стадия Integrate) */}
        <OnboardingProvider>
          <AppLayout />
        </OnboardingProvider>
      </RequireAuth>
    ),
    errorElement: <RouteErrorScreen full />,
    children: [
      {
        errorElement: <RouteErrorScreen />,
        children: [
          { index: true, element: <HomeRedirect /> },
          {
            path: 'dashboard',
            // стартовая страница: без аналитики — в первый доступный раздел, а не на /403
            element: (
              <RequirePermission permission="reports:read" redirectHome>
                <DashboardPage />
              </RequirePermission>
            ),
          },
          // отчёт «Активность КАМов» (ux.md §6.4): область — report_scope на бэке
          {
            path: 'reports/kam-activity',
            element: guarded('reports:read', <KamActivityPage />),
          },
          { path: 'board', element: guarded('requests:read', <BoardPage />) },
          { path: 'requests/:id', element: guarded('requests:read', <RequestPage />) },
          {
            path: 'registry/universities',
            element: guarded('universities:read', <UniversitiesPage />),
          },
          {
            path: 'registry/universities/:id',
            element: guarded('universities:read', <UniversityDetailPage />),
          },
          { path: 'registry/contracts', element: guarded('contracts:read', <ContractsPage />) },
          {
            path: 'registry/contracts/:id',
            element: guarded('contracts:read', <ContractDetailPage />),
          },
          { path: 'registry/products', element: guarded('products:read', <ProductsPage />) },
          {
            path: 'registry/products/:id',
            element: guarded('products:read', <ProductDetailPage />),
          },
          { path: 'registry/programs', element: guarded('programs:read', <ProgramsPage />) },
          {
            path: 'registry/programs/:id',
            element: guarded('programs:read', <ProgramDetailPage />),
          },
          { path: 'talent-pool', element: guarded('students:read', <TalentPoolPage />) },
          {
            path: 'talent-pool/students/:id',
            element: guarded('students:read', <StudentPage />),
          },
          { path: 'workflow', element: guarded('workflow:read', <WorkflowPage />) },
          { path: 'import', element: guarded('import:run', <ImportPage />) },
          {
            path: 'settings/notifications',
            element: guarded('notifications:self', <NotificationSettingsPage />),
          },
          // вход в «Администрирование» — первый доступный пункт группы
          { path: 'admin', element: <HomeRedirect group="admin" /> },
          { path: 'admin/users', element: guarded('admin:users', <AdminUsersPage />) },
          { path: 'admin/roles', element: guarded('admin:roles', <AdminRolesPage />) },
          { path: 'admin/roles/new', element: guarded('admin:roles', <AdminRoleEditorPage />) },
          {
            path: 'admin/roles/:roleId',
            element: guarded('admin:roles', <AdminRoleEditorPage />),
          },
          {
            path: 'admin/dictionaries',
            element: guarded('admin:dictionaries', <AdminDictionariesPage />),
          },
          {
            path: 'admin/feature-flags',
            element: guarded('admin:flags', <AdminFeatureFlagsPage />),
          },
          {
            path: 'admin/approvals',
            element: guarded('workflow:approve', <AdminApprovalsPage />),
          },
          {
            path: 'admin/integrations',
            element: guarded('admin:audit', <AdminIntegrationsPage />),
          },
          { path: 'admin/audit', element: guarded('admin:audit', <AdminAuditPage />) },
          // встроенная справка: пользователю — всем вошедшим, администратору — по правам
          { path: 'help', element: <HelpPage /> },
          {
            path: 'help/admin',
            element: (
              <RequirePermission anyOf={ADMIN_GUIDE_PERMISSIONS}>
                <AdminHelpPage />
              </RequirePermission>
            ),
          },
          { path: '403', element: <ForbiddenPage /> },
          { path: '*', element: <NotFoundPage /> },
        ],
      },
    ],
  },
]);
