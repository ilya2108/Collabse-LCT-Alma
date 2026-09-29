import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Bell } from 'lucide-react';
import { motion, useReducedMotion } from 'motion/react';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { NotificationsEmptyIllustration } from '@/app/localIllustrations';
import { listNotifications, markNotificationsRead } from '@/shared/api/endpoints/notifications';
import type { NotificationFeedItem } from '@/shared/api/types';
import { cn } from '@/shared/lib/cn';
import { formatRelative } from '@/shared/lib/format';
import { motionTokens } from '@/shared/lib/motion';
import { toastError } from '@/shared/lib/toast';
import { useSseInvalidate } from '@/shared/lib/useSseInvalidate';
import { Button } from '@/shared/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/shared/ui/popover';
import { Skeleton } from '@/shared/ui/skeleton';

/**
 * Колокольчик нотификаций в хедере (redesign.md §6.2): поповер с последними
 * 10 событиями компактной лентой, счётчик непрочитанных, пустое состояние —
 * иллюстрация `notifications-empty` compact. Realtime — SSE
 * `notification.created`, при новом событии — one-shot покачивание (§2.3).
 * Функциональность (запросы, отметка прочитанным) — как в ux.md §3.3.
 */

export function NotificationsBell(): ReactNode {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const reduced = useReducedMotion() ?? false;
  const [open, setOpen] = useState(false);

  const feedQuery = useQuery({
    queryKey: ['notifications-feed'],
    queryFn: ({ signal }) => listNotifications({ limit: 10, signal }),
    staleTime: 30_000,
    retry: 1,
  });
  useSseInvalidate('notification.created', [['notifications-feed']]);

  const items = feedQuery.data?.items ?? [];
  const unreadCount = items.filter((n) => !n.is_read).length;

  // One-shot покачивание при росте непрочитанных (§2.3 «Микро-фидбек»).
  const prevUnread = useRef(unreadCount);
  const [ringKey, setRingKey] = useState(0);
  useEffect(() => {
    if (unreadCount > prevUnread.current) setRingKey((k) => k + 1);
    prevUnread.current = unreadCount;
  }, [unreadCount]);

  const readMutation = useMutation({
    mutationFn: (payload: { ids: string[] } | { all: true }) => markNotificationsRead(payload),
    meta: { silent: true },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['notifications-feed'] });
    },
    onError: (error) => toastError(error, { title: 'Не удалось отметить прочитанным' }),
  });

  const openItem = (item: NotificationFeedItem): void => {
    if (!item.is_read) readMutation.mutate({ ids: [item.id] });
    setOpen(false);
    if (item.url) navigate(item.url);
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          data-tour="header-bell"
          aria-label={
            unreadCount > 0 ? `Уведомления, непрочитанных: ${unreadCount}` : 'Уведомления'
          }
          className="relative text-muted-foreground hover:text-foreground"
        >
          <motion.span
            key={ringKey}
            animate={ringKey > 0 && !reduced ? { rotate: [0, -12, 10, -6, 0] } : undefined}
            transition={{ duration: motionTokens.duration.slow - 0.1 }}
            className="inline-flex origin-top"
          >
            <Bell className="size-4.5" aria-hidden="true" />
          </motion.span>
          {unreadCount > 0 ? (
            <span
              aria-hidden="true"
              className="tabular absolute right-0.5 top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-status-danger px-1 text-[10px] font-semibold leading-none text-white"
            >
              {unreadCount > 9 ? '9+' : unreadCount}
            </span>
          ) : null}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[340px] p-0">
        <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-2.5">
          <span className="text-sm font-semibold">Уведомления</span>
          {unreadCount > 0 ? (
            <Button
              variant="link"
              size="sm"
              className="h-auto p-0 text-xs"
              disabled={readMutation.isPending}
              onClick={() => readMutation.mutate({ all: true })}
            >
              Прочитать все
            </Button>
          ) : null}
        </div>
        {feedQuery.isLoading ? (
          <div className="flex flex-col gap-3 px-4 py-4">
            {[0, 1, 2].map((i) => (
              <div key={i} className="flex items-start gap-3">
                <Skeleton className="mt-1 size-2 rounded-full" />
                <div className="flex-1 space-y-1.5">
                  <Skeleton className="h-3.5 w-full rounded-sm" />
                  <Skeleton className="h-3 w-1/3 rounded-sm" />
                </div>
              </div>
            ))}
          </div>
        ) : feedQuery.isError ? (
          <p className="px-4 py-6 text-center text-sm text-muted-foreground">
            Лента уведомлений недоступна
          </p>
        ) : items.length === 0 ? (
          <div className="flex flex-col items-center gap-2 px-4 py-6 text-center">
            <NotificationsEmptyIllustration height={96} />
            <p className="text-sm text-muted-foreground">Уведомлений пока нет</p>
          </div>
        ) : (
          <ul className="max-h-80 overflow-y-auto py-1">
            {items.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  onClick={() => openItem(item)}
                  className={cn(
                    'flex w-full items-start gap-3 px-4 py-2.5 text-left transition-colors duration-150 hover:bg-primary-tint',
                    !item.url && 'cursor-default',
                  )}
                >
                  <span
                    aria-hidden="true"
                    className={cn(
                      'mt-1.5 size-2 shrink-0 rounded-full',
                      item.is_read ? 'bg-border' : 'bg-primary',
                    )}
                  />
                  <span className="min-w-0 flex-1">
                    <span className={cn('block text-sm', !item.is_read && 'font-medium')}>
                      {item.text}
                    </span>
                    <span className="block text-xs text-muted-foreground">
                      {formatRelative(item.created_at)}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </PopoverContent>
    </Popover>
  );
}
