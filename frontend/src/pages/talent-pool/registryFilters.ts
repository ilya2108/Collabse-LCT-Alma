/**
 * Стартовые фильтры реестра talent pool из адресной строки (без импортов —
 * юнит-тестируется через node --test):
 * - `?status=` — сегмент воронки (уже проверен по FUNNEL_ORDER вызывающим);
 * - `?university=` — ссылка «Студенты вуза (N)» из карточки вуза. Раньше параметр
 *   не читался, и ссылка открывала всех студентов вместо N студентов вуза.
 * Не-UUID в `university` игнорируется: сервер ответил бы 422, и реестр показал бы ошибку.
 */

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function universityParamId(value: string | null): string | null {
  const trimmed = value?.trim() ?? '';
  return UUID_RE.test(trimmed) ? trimmed : null;
}

export function registryDefaultFilters(
  funnelStatus: string | null,
  universityParam: string | null,
): Record<string, string> {
  const filters: Record<string, string> = {};
  if (funnelStatus) filters.funnel_status = funnelStatus;
  const universityId = universityParamId(universityParam);
  if (universityId) filters.university_id = universityId;
  return filters;
}
