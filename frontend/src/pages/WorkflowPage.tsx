import { Suspense, lazy, type ReactNode } from 'react';
import { LocalLoadingState } from '@/components/wp5/PageChrome';

/**
 * Конструктор workflow грузится лениво: @xyflow/react — тяжёлая зависимость,
 * ей не место в основном бандле (redesign.md §8.3 — lazy-чанк сохранён).
 */
const WorkflowConstructorPage = lazy(() => import('./workflow/WorkflowConstructorPage'));

export function WorkflowPage(): ReactNode {
  return (
    <Suspense fallback={<LocalLoadingState rows={10} />}>
      <WorkflowConstructorPage />
    </Suspense>
  );
}
