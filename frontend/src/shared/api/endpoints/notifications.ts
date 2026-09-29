import { api } from '../client';
import type {
  ListEnvelope,
  NotificationChannelCode,
  NotificationChannelInfo,
  NotificationFeedItem,
  NotificationSettings,
  TelegramLinkCode,
} from '../types';

/**
 * Нотификации пользователя (api-contract.md §9.1–9.2): настройки каналов и
 * подписок, in-app лента для колокольчика, тестовая отправка и привязка
 * Telegram по одноразовому коду.
 */

export function getNotificationSettings(signal?: AbortSignal): Promise<NotificationSettings> {
  return api.get<NotificationSettings>('/notifications/settings', { signal });
}

export function putNotificationSettings(
  settings: NotificationSettings,
): Promise<NotificationSettings> {
  return api.put<NotificationSettings>('/notifications/settings', { body: settings });
}

export function listNotificationChannels(
  signal?: AbortSignal,
): Promise<{ items: NotificationChannelInfo[] }> {
  return api.get<{ items: NotificationChannelInfo[] }>('/notifications/channels', { signal });
}

/** Тестовая отправка «Проверка связи» в выбранный канал (§9.1). */
export function sendTestNotification(channel: NotificationChannelCode): Promise<void> {
  return api.post<void>('/notifications/test', { body: { channel } });
}

export function listNotifications(
  options: { unread?: boolean; limit?: number; signal?: AbortSignal } = {},
): Promise<ListEnvelope<NotificationFeedItem>> {
  return api.get<ListEnvelope<NotificationFeedItem>>('/notifications', {
    query: { unread: options.unread || undefined, limit: options.limit ?? 10 },
    signal: options.signal,
  });
}

export function markNotificationsRead(
  payload: { ids: string[] } | { all: true },
): Promise<void> {
  return api.post<void>('/notifications/read', { body: payload });
}

/** Код 6 цифр, TTL 10 минут; повторный запрос инвалидирует предыдущий (§9.2). */
export function createTelegramLinkCode(): Promise<TelegramLinkCode> {
  return api.post<TelegramLinkCode>('/notifications/telegram/link-code');
}

export function unlinkTelegram(): Promise<void> {
  return api.delete<void>('/notifications/telegram/link');
}
