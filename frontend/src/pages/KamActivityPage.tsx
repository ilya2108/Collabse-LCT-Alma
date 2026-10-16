import { Suspense, lazy, type ReactNode } from 'react';
import { LoadingState } from '@/shared/ui/data-table';

/**
 * Отчёт «Активность КАМов» грузится лениво: ECharts — тяжёлая зависимость, Vite
 * вырежет экран в отдельный чанк (та же схема, что у дашборда).
 */
const KamActivityScreen = lazy(() => import('./reports/KamActivityScreen'));

export function KamActivityPage(): ReactNode {
  return (
    <Suspense fallback={<LoadingState rows={10} />}>
      <KamActivityScreen />
    </Suspense>
  );
}

export default KamActivityPage;
