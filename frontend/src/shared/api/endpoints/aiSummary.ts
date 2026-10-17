import { api } from '../client';
import type { WorkflowType } from '../types';

/**
 * «Сводка ИИ» отчёта «Активность КАМов» — `/reports/ai-summaries` (api-contract.md §7.2.3).
 * Текстовая сводка ФАКТОВ поверх уже посчитанных цифр отчётов (команда или один КАМ):
 * не агент и не советчик. GigaChat получает только агрегаты и псевдонимы «КАМ-01…»,
 * ФИО подставляет CRM при чтении; без GigaChat — сводка по правилам (`mode: "rules"`).
 * Задача асинхронная: POST → 202 `queued` (или 200 из кэша на 1 ч / присоединение к
 * задаче в полёте / сразу `done` по правилам), дальше опрос GET раз в `poll_after_ms`.
 *
 * Типы — зеркало backend/app/modules/ai_summary/schemas.py; живут здесь, а не в
 * types.ts, чтобы не пересекаться с другими доработками.
 */

export type AiSummaryKind = 'team' | 'kam';
export type AiSummaryStatus = 'queued' | 'running' | 'done' | 'failed';
/** `auto` — провайдер из настроек стенда (GigaChat), `rules` — шаблон из цифр без ИИ. */
export type AiSummaryMode = 'auto' | 'rules';
export type AiSummaryProvider = 'gigachat' | 'rules' | 'stub';

/** Коды сбоя задачи (поле `error.code`, не HTTP), api-contract.md §7.2.3. */
export type AiSummaryErrorCode =
  | 'llm_unavailable'
  | 'llm_busy'
  | 'llm_timeout'
  | 'llm_not_configured'
  | 'llm_budget_exceeded'
  | 'llm_bad_output'
  | 'interrupted'
  /** Лимит новых сводок GigaChat на пользователя за час. */
  | 'rate_limited'
  /** Флаг `ai_summaries` выключили, пока задача ждала очереди. */
  | 'disabled'
  | 'internal'
  | (string & {});

/** Фильтры отчёта — те же ключи и смысл, что у `GET /reports/kam-activity`. */
export interface AiSummaryParams {
  from?: string | null;
  to?: string | null;
  workflow_type?: WorkflowType | null;
  kam_id?: string[];
  university_id?: string[];
  direction_id?: string[];
}

export interface AiSummaryCreate {
  kind: AiSummaryKind;
  /** Обязателен при `kind: "kam"`. */
  kam_id: string | null;
  params: AiSummaryParams;
  mode: AiSummaryMode;
  /** «Обновить» — мимо кэша 1 ч (к задаче в полёте всё равно присоединяется). */
  force: boolean;
}

export interface AiSummarySections {
  headline: string;
  going_well: string[];
  lagging: string[];
  attention: string[];
  questions: string[];
}

/** Упомянутый КАМ: псевдоним, под которым он ушёл в GigaChat, и подставленное имя. */
export interface AiSummaryPerson {
  alias: string;
  kam_id: string;
  name: string;
}

/** Удалённые проверкой фразы по причинам. */
export interface AiSummaryRemoved {
  /** Число или дата, которых нет в данных отчёта. */
  numbers: number;
  /** Совет вместо факта. */
  advice: number;
  /** Оценка людей или работы; утверждение про КАМа без цифр. */
  evaluative: number;
  /** Неизвестный псевдоним, ссылка, вопрос без «?», длинная фраза. */
  other: number;
}

/** Сверка текста с отчётом: числа и фразы, удалённые проверкой. */
export interface AiSummaryChecks {
  numbers_total: number;
  numbers_matched: number;
  sentences_removed: number;
  advice_removed: number;
  /** Нет у сводок, сформированных до разбивки по причинам. */
  removed_by_reason?: AiSummaryRemoved;
}

export interface AiSummaryResult {
  /** Фактический источник текста (при сбое GigaChat может отличаться от `provider`). */
  source: AiSummaryProvider;
  /** null — сводка по правилам. */
  model: string | null;
  generated_at: string;
  /** ФИО уже подставлены на стороне CRM. */
  sections: AiSummarySections;
  /** Только для `kind: "team"`. */
  people?: AiSummaryPerson[];
  checks: AiSummaryChecks;
}

export interface AiSummaryError {
  code: AiSummaryErrorCode;
  message: string;
  rules_available: boolean;
}

export interface AiSummaryJob {
  id: string;
  kind: AiSummaryKind;
  kam_id: string | null;
  /** «Фамилия И. О.» при `kind: "kam"`. */
  kam_name: string | null;
  status: AiSummaryStatus;
  mode: AiSummaryMode;
  /** Запланированный провайдер. */
  provider: AiSummaryProvider;
  /** Эхо параметров после нормализации (`from` и `to` заполнены). */
  params: AiSummaryParams;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  /** Сколько сводок GigaChat стоит в очереди раньше этой; null — не в очереди. */
  queue_ahead: number | null;
  /** Через сколько опросить снова; null — задача завершена. */
  poll_after_ms: number | null;
  /** Ответ — уже существующая задача (кэш 1 ч или дедупликация). */
  reused: boolean;
  result: AiSummaryResult | null;
  error: AiSummaryError | null;
}

/** 202 — новая задача, 200 — готовая из кэша / задача в полёте / сводка по правилам. */
export function createAiSummary(body: AiSummaryCreate, signal?: AbortSignal): Promise<AiSummaryJob> {
  return api.post<AiSummaryJob>('/reports/ai-summaries', { body, signal });
}

/** 404 — задачи нет или она в чужой области. */
export function getAiSummary(jobId: string, signal?: AbortSignal): Promise<AiSummaryJob> {
  return api.get<AiSummaryJob>(`/reports/ai-summaries/${encodeURIComponent(jobId)}`, { signal });
}
