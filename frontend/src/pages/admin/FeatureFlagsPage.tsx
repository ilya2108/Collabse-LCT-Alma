import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Info } from 'lucide-react';
import type { ReactNode } from 'react';
import { useOnboarding } from '@/features/onboarding';
import { listFeatureFlags, patchFeatureFlag } from '@/shared/api/endpoints/admin';
import { notifyApiError } from '@/shared/api/feedback';
import { formatDateTime } from '@/shared/lib/format';
import { useSseInvalidate } from '@/shared/lib/useSseInvalidate';
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
} from '@/shared/ui/data-table';
import { Switch } from '@/shared/ui/switch';

/**
 * Фичефлаги (ux.md §14.3, redesign.md §6.9): строки-карточки с Switch —
 * переключение мгновенно публикуется всем клиентам по SSE `flags.updated`.
 * `ai_summaries` — кнопка «Сводка ИИ» в отчёте «Активность КАМов» (заменил пустой
 * заблокированный `llm_features`): без GigaChat работает сводка по правилам.
 */

/** Русские описания известных флагов MVP (сервер может отдавать свои). */
const FLAG_DESCRIPTIONS: Record<string, string> = {
  auto_ranking:
    'Расчётный спрос программ: индекс 0–100 по заявкам, обучающимся и параллельным потокам (справочно, ручной приоритет не меняет)',
  report_pdf:
    'Серверная PDF-выгрузка таблиц отчётов (report:<code>); PDF графиков панели — на клиенте, флагом не закрыт',
  messenger_attachments: 'Вложения в комментариях заявок',
  max_channel: 'Канал уведомлений Max (заглушка интеграции)',
  ai_summaries:
    'Сводки ИИ в отчёте «Активность КАМов»: GigaChat получает только агрегаты и псевдонимы КАМов; без GigaChat — сводка по правилам',
};

export function AdminFeatureFlagsPage(): ReactNode {
  const queryClient = useQueryClient();
  // Чек-лист онбординга §7.5: no-op без провайдера.
  const { completeChecklistItem } = useOnboarding();
  const flagsQuery = useQuery({
    queryKey: ['admin-feature-flags'],
    queryFn: ({ signal }) => listFeatureFlags(signal),
  });
  useSseInvalidate('flags.updated', [['admin-feature-flags'], ['flags']]);

  const toggleMutation = useMutation({
    mutationFn: ({ name, enabled }: { name: string; enabled: boolean }) =>
      patchFeatureFlag(name, enabled),
    meta: { silent: true },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-feature-flags'] });
      void queryClient.invalidateQueries({ queryKey: ['flags'] });
      completeChecklistItem('toggle-flag');
    },
    onError: (error) => {
      notifyApiError(error, 'Не удалось переключить флаг');
      void queryClient.invalidateQueries({ queryKey: ['admin-feature-flags'] });
    },
  });

  let body: ReactNode;
  if (flagsQuery.isLoading) {
    body = <LoadingState rows={5} card={false} />;
  } else if (flagsQuery.isError) {
    body = (
      <ErrorState
        error={flagsQuery.error}
        onRetry={() => void flagsQuery.refetch()}
        title="Не удалось загрузить флаги"
      />
    );
  } else if ((flagsQuery.data ?? []).length === 0) {
    body = <EmptyState illustration="registry-empty" title="Флагов нет" />;
  } else {
    body = (
      <ul className="grid gap-2">
        {(flagsQuery.data ?? []).map((flag) => {
          const control = (
            <Switch
              checked={flag.enabled}
              disabled={toggleMutation.isPending && toggleMutation.variables?.name === flag.name}
              aria-label={`Флаг ${flag.name}`}
              onCheckedChange={(checked) => toggleMutation.mutate({ name: flag.name, enabled: checked })}
            />
          );
          return (
            <li
              key={flag.name}
              className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border border-border bg-card px-4 py-3 shadow-card"
            >
              <div className="min-w-0 flex-1">
                <p className="flex items-center gap-2 font-mono text-sm font-medium">{flag.name}</p>
                {/* длинные описания (ai_summaries) — в две строки, целиком — в подсказке */}
                <p
                  className="line-clamp-2 text-pretty text-sm text-muted-foreground"
                  title={flag.description || FLAG_DESCRIPTIONS[flag.name] || undefined}
                >
                  {flag.description || FLAG_DESCRIPTIONS[flag.name] || '—'}
                </p>
              </div>
              <span className="hidden text-xs text-muted-foreground md:block">
                {flag.updated_at
                  ? `${flag.updated_by?.full_name ?? '—'} · ${formatDateTime(flag.updated_at)}`
                  : '—'}
              </span>
              {control}
            </li>
          );
        })}
      </ul>
    );
  }

  return (
    <>
      <PageHeader title="Фичефлаги" subtitle="Переключение применяется мгновенно у всех клиентов" />
      <div className="mb-4 flex items-start gap-2 rounded-lg border border-border bg-primary-tint px-4 py-3 text-sm">
        <Info className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden="true" />
        <p>
          Переключение флага рассылается всем открытым клиентам по SSE (flags.updated) — интерфейс
          перестраивается без перезагрузки страницы.
        </p>
      </div>
      {body}
    </>
  );
}
