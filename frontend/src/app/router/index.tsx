import { useEffect } from 'react';
import type { ReactNode } from 'react';
import { createBrowserRouter, Navigate, useRouteError } from 'react-router-dom';
import { AppLayout } from '@/app/layout/AppLayout';
import { isApiError } from '@/shared/api/errors';
import { ErrorState } from '@/shared/ui/data-table';
import { ADMIN_ONLY, IMPORT_ROLES } from '@/shared/auth/roles';
import {
  AdminApprovalsPage,
  AdminAuditPage,
  AdminDictionariesPage,
  AdminFeatureFlagsPage,
  AdminIntegrationsPage,
  AdminUsersPage,
} from '@/pages/admin/AdminPages';
import { BoardPage } from '@/pages/board/BoardPage';
import { DashboardPage } from '@/pages/DashboardPage';
import { ForbiddenPage, NotFoundPage } from '@/pages/ErrorPages';
import { ImportPage } from '@/pages/ImportPage';
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
import { RequireAuth, RequireRole } from './guards';

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

/**
 * Маршрутизация (таблица маршрутов — ux.md §3.2). Роли guard'ов совпадают
 * с menuConfig: скрытый пункт меню страхуется редиректом на /403.
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
          { index: true, element: <Navigate to="/dashboard" replace /> },
          { path: 'dashboard', element: <DashboardPage /> },
          { path: 'board', element: <BoardPage /> },
          { path: 'requests/:id', element: <RequestPage /> },
          { path: 'registry/universities', element: <UniversitiesPage /> },
          { path: 'registry/universities/:id', element: <UniversityDetailPage /> },
          { path: 'registry/contracts', element: <ContractsPage /> },
          { path: 'registry/contracts/:id', element: <ContractDetailPage /> },
          { path: 'registry/products', element: <ProductsPage /> },
          { path: 'registry/products/:id', element: <ProductDetailPage /> },
          { path: 'registry/programs', element: <ProgramsPage /> },
          { path: 'registry/programs/:id', element: <ProgramDetailPage /> },
          { path: 'talent-pool', element: <TalentPoolPage /> },
          { path: 'talent-pool/students/:id', element: <StudentPage /> },
          { path: 'workflow', element: <WorkflowPage /> },
          {
            path: 'import',
            element: (
              <RequireRole roles={IMPORT_ROLES}>
                <ImportPage />
              </RequireRole>
            ),
          },
          { path: 'settings/notifications', element: <NotificationSettingsPage /> },
          {
            path: 'admin',
            element: (
              <RequireRole roles={ADMIN_ONLY}>
                <Navigate to="/admin/users" replace />
              </RequireRole>
            ),
          },
          {
            path: 'admin/users',
            element: (
              <RequireRole roles={ADMIN_ONLY}>
                <AdminUsersPage />
              </RequireRole>
            ),
          },
          {
            path: 'admin/dictionaries',
            element: (
              <RequireRole roles={ADMIN_ONLY}>
                <AdminDictionariesPage />
              </RequireRole>
            ),
          },
          {
            path: 'admin/feature-flags',
            element: (
              <RequireRole roles={ADMIN_ONLY}>
                <AdminFeatureFlagsPage />
              </RequireRole>
            ),
          },
          {
            path: 'admin/approvals',
            element: (
              <RequireRole roles={ADMIN_ONLY}>
                <AdminApprovalsPage />
              </RequireRole>
            ),
          },
          {
            path: 'admin/integrations',
            element: (
              <RequireRole roles={ADMIN_ONLY}>
                <AdminIntegrationsPage />
              </RequireRole>
            ),
          },
          {
            path: 'admin/audit',
            element: (
              <RequireRole roles={ADMIN_ONLY}>
                <AdminAuditPage />
              </RequireRole>
            ),
          },
          { path: '403', element: <ForbiddenPage /> },
          { path: '*', element: <NotFoundPage /> },
        ],
      },
    ],
  },
]);
