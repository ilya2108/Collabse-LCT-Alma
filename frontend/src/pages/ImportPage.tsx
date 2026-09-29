import { Suspense, lazy, type ReactNode } from 'react';
import { LoadingState } from '@/shared/ui/data-table';

/**
 * Маршрут /import — четырёхшаговый мастер импорта (ux.md §11).
 * Ленивый чанк (redesign.md §8.3) со скелетоном-фолбэком §2.3.
 */
const ImportWizardPage = lazy(() =>
  import('./import/ImportWizardPage').then((m) => ({ default: m.ImportWizardPage })),
);

export function ImportPage(): ReactNode {
  return (
    <Suspense fallback={<LoadingState rows={8} />}>
      <ImportWizardPage />
    </Suspense>
  );
}
