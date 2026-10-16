import { Suspense, lazy, type ReactNode } from 'react';
import { LoadingState } from '@/shared/ui/data-table';

/**
 * Справка грузится лениво: текст руководств не нужен в основном бандле
 * (та же схема, что у дашборда и конструктора).
 */
const HelpScreen = lazy(() => import('./HelpScreen'));

/** Руководство пользователя — /help, доступно всем вошедшим. */
export function HelpPage(): ReactNode {
  return (
    <Suspense fallback={<LoadingState rows={10} />}>
      <HelpScreen guideId="user" />
    </Suspense>
  );
}

/** Руководство администратора — /help/admin, guard по правам администратора в роутере. */
export function AdminHelpPage(): ReactNode {
  return (
    <Suspense fallback={<LoadingState rows={10} />}>
      <HelpScreen guideId="admin" />
    </Suspense>
  );
}
