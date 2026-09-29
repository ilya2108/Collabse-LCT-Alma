import { dayjs } from './dayjs';

/** Пустые значения показываем как «—», не «null» (ux.md §4.4). */
export const EMPTY_VALUE = '—';

export function formatDate(value: string | null | undefined): string {
  if (!value) return EMPTY_VALUE;
  const d = dayjs(value);
  return d.isValid() ? d.format('D MMM YYYY') : EMPTY_VALUE;
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return EMPTY_VALUE;
  const d = dayjs(value);
  return d.isValid() ? d.format('D MMM YYYY, HH:mm') : EMPTY_VALUE;
}

/** Относительное время: «5 мин назад» до 24 часов, дальше — дата (ux.md §4.4). */
export function formatRelative(value: string | null | undefined): string {
  if (!value) return EMPTY_VALUE;
  const d = dayjs(value);
  if (!d.isValid()) return EMPTY_VALUE;
  return dayjs().diff(d, 'hour') < 24 ? d.fromNow() : formatDate(value);
}

const moneyFormatter = new Intl.NumberFormat('ru-RU', {
  maximumFractionDigits: 0,
  minimumFractionDigits: 0,
});

/**
 * Деньги: «1 250 000 ₽» с неразрывными пробелами-разрядами (ux.md §4.4).
 * API отдаёт суммы строкой decimal (api-contract.md §1.3).
 */
export function formatMoney(
  value: string | number | null | undefined,
  currency: string = 'RUB',
): string {
  if (value === null || value === undefined || value === '') return EMPTY_VALUE;
  const num = typeof value === 'string' ? Number(value) : value;
  if (!Number.isFinite(num)) return EMPTY_VALUE;
  const sign = currency === 'RUB' ? '₽' : currency;
  return `${moneyFormatter.format(num)} ${sign}`;
}

/** Текстовое значение или «—». */
export function formatText(value: string | null | undefined): string {
  return value ? value : EMPTY_VALUE;
}
