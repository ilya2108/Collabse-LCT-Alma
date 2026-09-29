import { useQueryClient, type QueryKey } from '@tanstack/react-query';
import { useEffect } from 'react';
import { sseClient, type StreamEvent } from '@/shared/api/sse';

/**
 * Подписка на SSE-топик с инвалидацией query-ключей react-query
 * (realtime-контур ux.md §7.2, §8.1; топики — api-contract.md §2.2).
 * `filter` позволяет реагировать только на свою сущность (например,
 * `request_id` в data события `request.comment_added`).
 */
export function useSseInvalidate(
  topic: string,
  keys: QueryKey[],
  filter?: (event: StreamEvent) => boolean,
): void {
  const queryClient = useQueryClient();
  useEffect(() => {
    return sseClient.subscribe(topic, (event) => {
      if (filter && !filter(event)) return;
      for (const key of keys) {
        void queryClient.invalidateQueries({ queryKey: key });
      }
    });
    // ключи сериализуемы — сравниваем по JSON, чтобы не пересоздавать подписку каждый рендер
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topic, queryClient, JSON.stringify(keys)]);
}

/** `data.request_id` из события SSE (топики request.*). */
export function eventRequestId(event: StreamEvent): string | null {
  if (typeof event.data === 'object' && event.data !== null && 'request_id' in event.data) {
    const value = (event.data as { request_id: unknown }).request_id;
    return typeof value === 'string' ? value : null;
  }
  return null;
}
