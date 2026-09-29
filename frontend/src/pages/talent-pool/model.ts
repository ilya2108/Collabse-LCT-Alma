import type { StudentFunnelStatus } from '@/shared/api/types';
import { STATUS_TRIPLES, TALENT_FUNNEL_RAMP } from '@/shared/config/tokens';

/**
 * Модель воронки студентов (api-contract.md §8.2, data-model.md §7.2):
 * ровно 4 статуса; вперёд — только на соседний, назад — на любой
 * с обязательным комментарием (исправление ошибок/отчисление).
 */

export const FUNNEL_ORDER: StudentFunnelStatus[] = [
  'candidate',
  'studying',
  'graduate',
  'talent_pool',
];

/**
 * Фиолетовый рамп talent pool (redesign.md §2.6, единый источник tokens.ts):
 * светлеет к концу воронки, identity по подписям сегментов — те же цвета,
 * что в графике-воронке дашборда. Текст/фон бейджей — через color-mix (§1.2).
 */
export const FUNNEL_META: Record<
  StudentFunnelStatus,
  { label: string; color: string; short: string }
> = {
  candidate: { label: 'Кандидаты', short: 'Кандидат', color: TALENT_FUNNEL_RAMP[0] },
  studying: { label: 'Обучаются', short: 'Обучается', color: TALENT_FUNNEL_RAMP[1] },
  graduate: { label: 'Выпускники', short: 'Выпускник', color: TALENT_FUNNEL_RAMP[2] },
  talent_pool: { label: 'Пул талантов', short: 'Пул талантов', color: TALENT_FUNNEL_RAMP[3] },
};

/**
 * Безопасный доступ к FUNNEL_META для статусов, пришедших с сервера:
 * неизвестный код (новый статус на бэке, мусор в данных) не роняет экран,
 * а показывается draft-серым бейджем с самим кодом вместо подписи.
 * Для статусов из FUNNEL_ORDER можно обращаться к FUNNEL_META напрямую.
 */
export function funnelMeta(
  status: StudentFunnelStatus,
): { label: string; color: string; short: string } {
  return (
    FUNNEL_META[status] ?? { label: status, short: status, color: STATUS_TRIPLES.draft.core }
  );
}

export function funnelIndex(status: StudentFunnelStatus): number {
  return FUNNEL_ORDER.indexOf(status);
}

/** Возврат назад по воронке — требует комментария (§8.2). */
export function isFunnelReturn(from: StudentFunnelStatus, to: StudentFunnelStatus): boolean {
  return funnelIndex(to) < funnelIndex(from);
}

/** Допустим ли переход: сосед вперёд или любой назад (§8.2). */
export function isFunnelMoveAllowed(
  from: StudentFunnelStatus,
  to: StudentFunnelStatus,
): boolean {
  if (from === to) return false;
  const delta = funnelIndex(to) - funnelIndex(from);
  return delta === 1 || delta < 0;
}

export const ACTIVITY_TYPE_LABELS: Record<string, string> = {
  course: 'Курс',
  event: 'Мероприятие',
  internship: 'Стажировка',
  hackathon: 'Хакатон',
  other: 'Другое',
};

export const ACTIVITY_FORMAT_LABELS: Record<string, string> = {
  online: 'Онлайн',
  offline: 'Очно',
  hybrid: 'Гибрид',
};

export const STUDENT_ACTIVITY_STATUS_LABELS: Record<string, string> = {
  registered: 'Записан',
  in_progress: 'Участвует',
  completed: 'Завершил',
  dropped: 'Выбыл',
};

/** Сколько секунд UI показывает раскрытые ПДн (ux.md §12.2). */
export const REVEAL_TTL_SECONDS = 60;
