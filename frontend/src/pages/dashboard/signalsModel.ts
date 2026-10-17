import type {
  LicenseSignal,
  LicenseSignalItem,
  SignalCount,
  UntouchedSignal,
  WorkflowCode,
} from '@/shared/api/endpoints/reports';

/**
 * Чистые функции карточки «Сигналы» на «Аналитике» (ux.md §6.1): тон по порогам,
 * подписи, процесс для перехода на доску. Без alias-импортов времени выполнения (только
 * `import type`) — модуль гоняется в `node --test` напрямую
 * (frontend/tests/dashboard-signals.test.ts).
 *
 * Тон — только у ненулевого сигнала: ноль показывается спокойно («нет»), без тревожного
 * цвета. Пороги — константы ниже, одни для всех ролей.
 */

export type SignalKey = 'stuck' | 'unassigned' | 'licenses' | 'untouched';
export type SignalTone = 'calm' | 'warning' | 'critical';

export interface ToneThresholds {
  /** С этого значения — «внимание». */
  warning: number;
  /** С этого значения — «критично». */
  critical: number;
}

/** Пороги счётных сигналов; у лицензий тон по составу (истёкшая — критично). */
export const SIGNAL_THRESHOLDS: Record<Exclude<SignalKey, 'licenses'>, ToneThresholds> = {
  stuck: { warning: 1, critical: 10 },
  unassigned: { warning: 1, critical: 5 },
  untouched: { warning: 1, critical: 15 },
};

const TONE_LABELS: Record<SignalTone, string> = {
  calm: 'норма',
  warning: 'внимание',
  critical: 'критично',
};

const WORKFLOW_ORDER: readonly WorkflowCode[] = ['b2b', 'b2c'];
const WORKFLOW_LABELS: Record<WorkflowCode, string> = { b2b: 'B2B', b2c: 'B2C' };

export function plural(n: number, forms: [string, string, string]): string {
  const abs = Math.abs(n) % 100;
  const last = abs % 10;
  if (abs > 10 && abs < 20) return forms[2];
  if (last === 1) return forms[0];
  if (last >= 2 && last <= 4) return forms[1];
  return forms[2];
}

export function countTone(value: number, thresholds: ToneThresholds): SignalTone {
  if (value <= 0) return 'calm'; // нулевой сигнал — спокойно при любых порогах
  if (value >= thresholds.critical) return 'critical';
  if (value >= thresholds.warning) return 'warning';
  return 'calm';
}

/** Истёкшая лицензия у действующего договора — критично, истекающая — внимание. */
export function licenseTone(signal: Pick<LicenseSignal, 'expiring' | 'expired'>): SignalTone {
  if (signal.expired > 0) return 'critical';
  if (signal.expiring > 0) return 'warning';
  return 'calm';
}

export function toneLabel(tone: SignalTone): string {
  return TONE_LABELS[tone];
}

/** Большое число плитки: ноль — словом «нет». */
export function signalValueText(value: number): string {
  return value > 0 ? String(value) : 'нет';
}

/** «B2B 19 · B2C 3» — только когда сигнал есть больше чем в одном процессе. */
export function workflowBreakdown(byWorkflow: SignalCount['by_workflow']): string {
  const parts = WORKFLOW_ORDER.filter((wf) => (byWorkflow[wf] ?? 0) > 0).map(
    (wf) => `${WORKFLOW_LABELS[wf]} ${byWorkflow[wf]}`,
  );
  return parts.length > 1 ? parts.join(' · ') : '';
}

/**
 * Процесс доски для перехода: выбранный на панели; иначе первый, где сигнал есть; иначе
 * первый доступный роли (доска показывает один процесс).
 */
export function targetWorkflow(
  byWorkflow: SignalCount['by_workflow'],
  selected: WorkflowCode | null,
  available: readonly WorkflowCode[],
): WorkflowCode {
  if (selected) return selected;
  const withSignal = WORKFLOW_ORDER.find(
    (wf) => available.includes(wf) && (byWorkflow[wf] ?? 0) > 0,
  );
  return withSignal ?? available[0] ?? 'b2b';
}

function withBreakdown(text: string, count: SignalCount): string {
  const breakdown = workflowBreakdown(count.by_workflow);
  return breakdown ? `${text} · ${breakdown}` : text;
}

export function stuckCaption(count: SignalCount): string {
  if (count.value <= 0) return 'Все открытые заявки в пределах нормы этапа';
  const noun = plural(count.value, ['заявка стоит', 'заявки стоят', 'заявок стоят']);
  return withBreakdown(`${noun} на этапе дольше нормы`, count);
}

export function unassignedCaption(count: SignalCount): string {
  if (count.value <= 0) return 'Все открытые заявки закреплены за КАМами';
  return withBreakdown('Ответственный — не КАМ процесса или деактивирован', count);
}

export function licenseValue(signal: Pick<LicenseSignal, 'expiring' | 'expired'>): number {
  return signal.expiring + signal.expired;
}

export function licenseCaption(
  signal: Pick<LicenseSignal, 'expiring' | 'expired' | 'horizon_days'>,
): string {
  const days = signal.horizon_days;
  if (licenseValue(signal) <= 0) return `В ближайшие ${days} дней лицензии не истекают`;
  return `Истекают за ${days} дн.: ${signal.expiring} · истекли: ${signal.expired}`;
}

export function untouchedCaption(signal: Pick<UntouchedSignal, 'value' | 'period'>): string {
  const days = signal.period.days;
  if (signal.value <= 0) return `Со всеми закреплёнными вузами был контакт за ${days} дн.`;
  return `Закреплены за КАМами, без взаимодействий за ${days} дн.`;
}

/** Срок лицензии в строке окна: «через 8 дней», «последний день — сегодня», «истекла 3 дня назад». */
export function daysLeftText(daysLeft: number): string {
  if (daysLeft === 0) return 'последний день — сегодня';
  const n = Math.abs(daysLeft);
  const days = `${n} ${plural(n, ['день', 'дня', 'дней'])}`;
  return daysLeft > 0 ? `через ${days}` : `истекла ${days} назад`;
}

const TRANSFER_LABELS: Record<NonNullable<LicenseSignalItem['transfer_status']>, string> = {
  not_started: 'передача не начата',
  in_progress: 'передача в работе',
  transferred: 'передана вузу',
};

export function transferStatusLabel(status: LicenseSignalItem['transfer_status']): string {
  return status ? TRANSFER_LABELS[status] : 'статус передачи не указан';
}

/** Подпись плитки для экранного диктора: название, значение, тон, пояснение, действие. */
export function signalAriaLabel(
  title: string,
  value: number,
  tone: SignalTone,
  caption: string,
  action: string,
): string {
  // пояснение — отдельным предложением: с заглавной, без двойной точки после «дн.»
  const sentence = caption.charAt(0).toUpperCase() + caption.slice(1).replace(/\.$/, '');
  return `${title}: ${signalValueText(value)}, ${toneLabel(tone)}. ${sentence}. ${action}`;
}
