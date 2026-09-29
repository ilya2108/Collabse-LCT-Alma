import { Suspense, lazy, type ReactNode } from 'react';
import { LoadingState } from '@/shared/ui/data-table';

/**
 * Дашборд грузится лениво: ECharts — тяжёлая зависимость, Vite вырежет её
 * в отдельный чанк (та же схема, что у конструктора workflow).
 */
const DashboardScreen = lazy(() => import('./dashboard/DashboardScreen'));

export function DashboardPage(): ReactNode {
  return (
    <Suspense fallback={<LoadingState rows={10} />}>
      <DashboardScreen />
    </Suspense>
  );
}
