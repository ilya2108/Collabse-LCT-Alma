import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { sseClient } from '@/shared/api/sse';
import type { CurrentUser } from '@/shared/api/types';
import { useAuth } from '@/shared/auth/AuthContext';
import { toastInfo } from '@/shared/lib/toast';
import { ROLE_CATALOG_QUERY_KEY } from '@/shared/lib/useRoleCatalog';

/**
 * Живое применение прав (F2, Р-F2-8): сервер после любого изменения ролей
 * рассылает SSE `access.changed` с `role_codes` и `user_ids`. Если событие
 * касается текущего пользователя — перечитываем /auth/me и все запросы:
 * меню, кнопки и доска перестраиваются без перезахода. Каталог ролей и экраны
 * администрирования ролей обновляются при любом событии.
 * Монтируется в AppLayout (внутри QueryClientProvider и AuthProvider).
 */

export const ACCESS_CHANGED_TOPIC = 'access.changed';

/** До этого момента тост «права обновлены» не показываем: изменение сделал сам пользователь. */
let ownChangeUntil = 0;

/**
 * Вызывают экраны администрирования ролей после своей мутации: событие
 * `access.changed`, пришедшее следом, не сопровождается тостом
 * «обновлены администратором» (администратор — это вы).
 */
export function noteOwnAccessChange(): void {
  ownChangeUntil = Date.now() + 10_000;
}

/** Запросы экранов, которые зависят от каталога ролей. */
const ROLE_DEPENDENT_KEYS = [
  ROLE_CATALOG_QUERY_KEY,
  ['admin-roles'],
  ['admin-role'],
  ['admin-users'],
  ['users-select'],
  ['registry', 'admin.users'],
  ['registry', 'admin.roles'],
] as const;

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string')
    : [];
}

/** Касается ли событие пользователя: его id в `user_ids` или общие роли в `role_codes`. */
export function accessChangeAffects(data: unknown, user: CurrentUser): boolean {
  if (typeof data !== 'object' || data === null) return true;
  const payload = data as { role_codes?: unknown; user_ids?: unknown };
  const roleCodes = stringList(payload.role_codes);
  const userIds = stringList(payload.user_ids);
  // событие без адресатов — общий сброс (например, аварийная команда grant-admin)
  if (roleCodes.length === 0 && userIds.length === 0) return true;
  return userIds.includes(user.id) || roleCodes.some((code) => user.roles.includes(code));
}

export function AccessChangeListener(): null {
  const queryClient = useQueryClient();
  const { user, reloadProfile } = useAuth();
  const userRef = useRef(user);
  userRef.current = user;

  useEffect(() => {
    const invalidateRoleScreens = (): void => {
      for (const key of ROLE_DEPENDENT_KEYS) {
        void queryClient.invalidateQueries({ queryKey: [...key] });
      }
    };

    const unsubscribe = sseClient.subscribe(ACCESS_CHANGED_TOPIC, (event) => {
      invalidateRoleScreens();
      const current = userRef.current;
      if (!current || !accessChangeAffects(event.data, current)) return;
      void reloadProfile().then(() => {
        // права изменились — все данные экранов перечитываются в новой области
        void queryClient.invalidateQueries();
        if (Date.now() > ownChangeUntil) {
          toastInfo(
            'Ваши права обновлены администратором',
            'Меню и доступные действия перестроены — перезаходить не нужно',
          );
        }
      });
    });

    // После обрыва SSE события могли потеряться — перечитываем профиль при реконнекте.
    let wasConnected: boolean | null = null;
    const unsubscribeConnection = sseClient.onConnectionChange((connected) => {
      if (connected && wasConnected === false) {
        invalidateRoleScreens();
        void reloadProfile();
      }
      wasConnected = connected;
    });

    return () => {
      unsubscribe();
      unsubscribeConnection();
    };
  }, [queryClient, reloadProfile]);

  return null;
}
