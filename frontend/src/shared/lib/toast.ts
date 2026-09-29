import { toast } from 'sonner';
import { errorMessage, isApiError } from '@/shared/api/errors';

/**
 * Обёртки над sonner (redesign.md §3.11): единый вид тостов нового стека.
 * Все message.* и notification.* AntD при миграции экранов заменяются этими
 * функциями. Toaster смонтирован в AppProviders (position="bottom-right").
 */

export function toastSuccess(message: string, description?: string): void {
  toast.success(message, { description });
}

interface ToastErrorOptions {
  title?: string;
  /** Кнопка «Повторить» (§3.11). */
  onRetry?: () => void;
}

export function toastError(error: unknown, options: ToastErrorOptions = {}): void {
  if (error instanceof DOMException && error.name === 'AbortError') return;
  const { title = 'Не удалось выполнить действие', onRetry } = options;
  const description = isApiError(error)
    ? [
        errorMessage(error),
        ...error.details.map((d) => d.message).filter(Boolean),
        error.traceId ? `Код обращения: ${error.traceId}` : null,
      ]
        .filter(Boolean)
        .join('\n')
    : errorMessage(error);
  toast.error(title, {
    description,
    duration: 5000,
    action: onRetry ? { label: 'Повторить', onClick: onRetry } : undefined,
  });
}

export function toastInfo(message: string, description?: string): void {
  toast.info(message, { description });
}
