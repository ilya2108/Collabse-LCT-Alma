import type { SemanticStatus } from '@/shared/config/tokens';

/**
 * Роли CRM (F2, Р-F2-1): назначаются в Альме, каталог ролей и их названия —
 * в БД (`GET /roles`, хук useRoleCatalog). Интерфейс проверяет не роли, а права
 * (`hasPermission`), поэтому здесь остались только запасные подписи системных
 * ролей (пока каталог не загружен) и тон бейджа роли.
 * Сервисные роли (svc_notification, svc_integration) в UI не встречаются.
 */

export const SYSTEM_ROLE_CODES = ['admin', 'head_kam', 'kam', 'observer'] as const;

export const SYSTEM_ROLE_LABELS: Record<string, string> = {
  admin: 'Администратор',
  head_kam: 'Руководитель КАМов',
  kam: 'КАМ',
  observer: 'Наблюдатель',
};

/** Название роли по коду без каталога: системная — по-русски, своя — код. */
export function fallbackRoleName(code: string): string {
  return SYSTEM_ROLE_LABELS[code] ?? code;
}

/** Тон бейджа роли: системные — по старшинству, свои роли — отдельным цветом. */
export function roleBadgeTone(code: string): SemanticStatus {
  switch (code) {
    case 'admin':
      return 'danger';
    case 'head_kam':
      return 'progress';
    case 'kam':
    case 'observer':
      return 'draft';
    default:
      return 'talent';
  }
}
