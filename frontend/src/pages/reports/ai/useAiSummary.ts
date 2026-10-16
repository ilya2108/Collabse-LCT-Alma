import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from 'react';
import {
  createAiSummary,
  getAiSummary,
  type AiSummaryCreate,
  type AiSummaryJob,
  type AiSummaryKind,
  type AiSummaryMode,
} from '@/shared/api/endpoints/aiSummary';
import type { KamActivityParams } from '@/shared/api/endpoints/kamActivity';
import { isApiError } from '@/shared/api/errors';
import { useAuth } from '@/shared/auth/AuthContext';
import { P } from '@/shared/auth/permissions';
import { toastError, toastInfo } from '@/shared/lib/toast';
import { useFlag } from '@/shared/lib/useFlags';
import {
  isPending,
  isPollExpired,
  isStale,
  paramsKey,
  pollInterval,
  requestBody,
  summaryTitle,
} from './aiSummaryModel';

/**
 * «Сводка ИИ» (ux.md §6.4): POST `/reports/ai-summaries` (useMutation) и опрос
 * GET `/reports/ai-summaries/{id}` (useQuery с `refetchInterval`), пока задача в очереди
 * или формируется. Кнопка видна при праве `reports:ai_summary` (в процессе фильтра; при
 * фильтре «все процессы» — во всех процессах, где роль видит отчёт, иначе сводка вышла бы
 * по части экрана и сервер ответит 403) и флаге `ai_summaries`; ответ 403 или 422 прячет её
 * для этих параметров. Состояние —
 * на «владельца» (команда или конкретный КАМ): переход к другому КАМу показывает его
 * кнопку без чужой сводки. «Скрыть» не отменяет задачу: готовая сводка лежит в кэше
 * сервера час, повторный клик вернёт её без нового вызова GigaChat.
 */

export interface AiSummaryStartOptions {
  mode?: AiSummaryMode;
  /** «Обновить» — мимо кэша 1 ч. */
  force?: boolean;
}

export interface AiSummaryController {
  kind: AiSummaryKind;
  /** Показывать кнопку: право, флаг и не было 403/422 для этих параметров. */
  available: boolean;
  /** Показывать панель. */
  open: boolean;
  /** POST в полёте. */
  creating: boolean;
  /** Создание или формирование — до результата. */
  busy: boolean;
  job: AiSummaryJob | null;
  /** Ошибка POST (кроме 403/422): панель предлагает повторить. */
  requestError: unknown;
  /** Опрос задачи не проходит (сеть) — показываем последнее известное состояние. */
  syncError: boolean;
  /** С момента клика (часы клиента). */
  elapsedMs: number;
  /** Фильтры страницы ушли от параметров сводки. */
  stale: boolean;
  /** Опрос остановлен по клиентскому пределу 15 мин. */
  pollExpired: boolean;
  /** Заголовок до ответа сервера. */
  placeholderTitle: string;
  panelRef: RefObject<HTMLElement>;
  start: (options?: AiSummaryStartOptions) => void;
  /** Прокрутить к панели и поставить на неё фокус. */
  reveal: () => void;
  close: () => void;
}

export interface UseAiSummaryOptions {
  kind: AiSummaryKind;
  /** Обязателен для `kind: "kam"`. */
  kamId: string | null;
  params: KamActivityParams;
  /** Дополнительное условие показа (например, не дублировать кнопку страницы). */
  enabled?: boolean;
  /** Имя КАМа для заголовка, пока сервер не ответил. */
  kamName?: string | null;
}

interface Session {
  seq: number;
  owner: string;
  jobId: string | null;
  startedAt: number;
  error: unknown;
}

interface CreateVars {
  body: AiSummaryCreate;
  seq: number;
  key: string;
}

const BLOCKING_STATUSES = new Set([403, 422]);

export function useAiSummary({
  kind,
  kamId,
  params,
  enabled = true,
  kamName = null,
}: UseAiSummaryOptions): AiSummaryController {
  const { hasPermission, workflowTypesWith } = useAuth();
  const flagOn = useFlag('ai_summaries');
  const queryClient = useQueryClient();
  const panelRef = useRef<HTMLElement>(null);
  const seqRef = useRef(0);

  const owner = `${kind}:${kamId ?? ''}`;
  const currentKey = `${owner}|${paramsKey(params, kind)}`;
  const [session, setSession] = useState<Session | null>(null);
  const [blockedKey, setBlockedKey] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  // «все процессы»: право на сводки должно покрывать все процессы, где роль видит отчёт
  const coversFilter = useMemo(() => {
    if (params.workflow_type) return true;
    const ai = workflowTypesWith(P.REPORTS_AI_SUMMARY);
    return workflowTypesWith(P.REPORTS_READ).every((wf) => ai.includes(wf));
  }, [params.workflow_type, workflowTypesWith]);
  const permitted =
    enabled &&
    flagOn &&
    hasPermission(P.REPORTS_AI_SUMMARY, params.workflow_type ?? null) &&
    coversFilter &&
    (kind === 'team' || Boolean(kamId));
  const available = permitted && blockedKey !== currentKey;
  const active = available && session !== null && session.owner === owner ? session : null;
  const jobId = active?.jobId ?? null;
  const startedAt = active?.startedAt ?? 0;

  const block = useCallback(
    (error: unknown, key: string) => {
      setBlockedKey(key);
      setSession(null);
      if (isApiError(error) && error.status === 422) {
        // флаг выключили — перечитать /flags, кнопка уйдёт и на других экранах
        void queryClient.invalidateQueries({ queryKey: ['flags'] });
      }
      toastError(error, { title: 'Сводка ИИ недоступна' });
    },
    [queryClient],
  );

  const { mutate } = useMutation({
    mutationFn: (vars: CreateVars) => createAiSummary(vars.body),
    meta: { silent: true },
    onSuccess: (job, vars) => {
      queryClient.setQueryData(['ai-summary', job.id], job);
      setSession((prev) => (prev && prev.seq === vars.seq ? { ...prev, jobId: job.id } : prev));
      if (vars.body.force && job.reused && job.status === 'done') {
        // «Обновить» не чаще раза в 5 минут: сервер вернул только что сформированную сводку
        toastInfo('Сводка уже свежая', 'Её сформировали меньше 5 минут назад — показана она.');
      }
    },
    onError: (error, vars) => {
      if (isApiError(error) && BLOCKING_STATUSES.has(error.status)) {
        block(error, vars.key);
        return;
      }
      setSession((prev) => (prev && prev.seq === vars.seq ? { ...prev, error } : prev));
    },
  });

  const jobQuery = useQuery({
    queryKey: ['ai-summary', jobId],
    queryFn: ({ signal }) => getAiSummary(jobId ?? '', signal),
    enabled: jobId !== null,
    refetchInterval: (query) => pollInterval(query.state.data, Date.now() - startedAt),
    refetchOnWindowFocus: false,
    retry: (count, error) =>
      !(isApiError(error) && [401, 403, 404, 422].includes(error.status)) && count < 2,
  });

  const job = jobId !== null ? (jobQuery.data ?? null) : null;
  const creating = active !== null && active.jobId === null && active.error == null;
  const elapsedMs = active ? Math.max(0, now - active.startedAt) : 0;
  const pollExpired = isPollExpired(job, elapsedMs);
  const busy = creating || (isPending(job?.status) && !pollExpired);

  // секундомер «Формируется · 23 с» — только пока ждём
  useEffect(() => {
    if (!busy) return undefined;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [busy]);

  // опрос: 403/422 — права или флаг сняли, 404 — задачи больше нет (ретенция, чужая область);
  // после обработки сессия сброшена (jobId = null), повторно эффект не срабатывает
  const pollError = jobQuery.error;
  useEffect(() => {
    if (jobId === null || !isApiError(pollError)) return;
    if (BLOCKING_STATUSES.has(pollError.status)) {
      block(pollError, currentKey);
    } else if (pollError.status === 404) {
      setSession(null);
      toastInfo('Сводка больше недоступна', 'Сформируйте её заново кнопкой «Сводка ИИ».');
    }
  }, [pollError, jobId, block, currentKey]);

  const reveal = useCallback(() => {
    const node = panelRef.current;
    if (!node) return;
    node.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    node.focus({ preventScroll: true });
  }, []);

  const start = useCallback(
    (options: AiSummaryStartOptions = {}) => {
      if (!available) return;
      seqRef.current += 1;
      const seq = seqRef.current;
      setSession({ seq, owner, jobId: null, startedAt: Date.now(), error: null });
      setNow(Date.now());
      mutate({ body: requestBody(kind, params, kamId, options), seq, key: currentKey });
      window.requestAnimationFrame(() => reveal());
    },
    [available, owner, mutate, kind, params, kamId, currentKey, reveal],
  );

  const close = useCallback(() => setSession(null), []);

  const placeholderTitle = useMemo(
    () => summaryTitle({ kind, kam_name: kamName, params }),
    [kind, kamName, params],
  );

  return {
    kind,
    available,
    open: active !== null,
    creating,
    busy,
    job,
    requestError: active?.error ?? null,
    syncError: jobQuery.isError && isPending(job?.status),
    elapsedMs,
    stale: isStale(job, params),
    pollExpired,
    placeholderTitle,
    panelRef,
    start,
    reveal,
    close,
  };
}
