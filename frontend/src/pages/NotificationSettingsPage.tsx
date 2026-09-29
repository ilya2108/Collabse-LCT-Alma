import type { ReactNode } from 'react';
import { NotificationSettingsScreen } from './notifications/NotificationSettingsScreen';

/** Маршрут /settings/notifications (ux.md §13). */
export function NotificationSettingsPage(): ReactNode {
  return <NotificationSettingsScreen />;
}
