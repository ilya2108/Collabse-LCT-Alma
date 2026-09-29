import type { ReactNode } from 'react';
import { EmptyState, ErrorState, LoadingState } from '@/shared/ui/data-table';

/**
 * Совместимость WP4 (стадия Integrate): состояния «загрузка / ошибка /
 * не найдено» — обёртки над общими компонентами §3.5 с иллюстрациями §4.
 */

/** Скелетон списка — та же геометрия, что контент (§2.3 shimmer). */
export function LoadingPanel({ rows = 5 }: { rows?: number }): ReactNode {
  return <LoadingState rows={rows} card={false} />;
}

/** Ошибка загрузки: сюжет error-broken, сообщение, requestId, «Повторить». */
export function ErrorPanel(props: {
  error: unknown;
  onRetry?: () => void;
  title?: string;
}): ReactNode {
  return <ErrorState {...props} />;
}

/** «Заявка не найдена» и подобные 404-состояния (§4.2 error-404). */
export function NotFoundPanel({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}): ReactNode {
  return (
    <EmptyState illustration="error-404" title={title} description={description} actions={action} />
  );
}
