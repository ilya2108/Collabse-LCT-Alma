import { Copy } from 'lucide-react';
import type { ReactNode } from 'react';
import { toastError, toastSuccess } from '@/shared/lib/toast';
import { Button } from '@/shared/ui/button';
import { Skeleton } from '@/shared/ui/skeleton';

/**
 * Итог вкладки «Просрочки»: детерминированная строка из API (тот же текст в A1 XLSX и
 * в PDF) крупно, кнопка «Скопировать» — вставить в письмо или чат руководству; под ней —
 * определение просрочки одной строкой.
 */

interface SlaHeadlineProps {
  headline: string | null;
  definition: string | null;
  loading?: boolean;
}

export function SlaHeadline({ headline, definition, loading = false }: SlaHeadlineProps): ReactNode {
  if (loading || headline === null) {
    return (
      <div className="flex flex-col gap-2 rounded-lg border border-border bg-card p-4 shadow-card sm:p-5">
        <Skeleton className="h-5 w-11/12 rounded-sm" />
        <Skeleton className="h-5 w-2/3 rounded-sm" />
        <Skeleton className="mt-1 h-3 w-1/2 rounded-sm" />
      </div>
    );
  }

  const copy = (): void => {
    const text = definition ? `${headline}\n${definition}` : headline;
    if (!navigator.clipboard) {
      toastError(new Error('Буфер обмена недоступен в этом браузере'), { title: 'Не удалось скопировать' });
      return;
    }
    navigator.clipboard
      .writeText(text)
      .then(() => toastSuccess('Итог скопирован'))
      .catch((error: unknown) => toastError(error, { title: 'Не удалось скопировать' }));
  };

  return (
    <section
      aria-label="Итог"
      className="flex flex-col gap-2 rounded-lg border border-border bg-card p-4 shadow-card sm:flex-row sm:items-start sm:gap-4 sm:p-5"
    >
      <div className="min-w-0 flex-1">
        <p className="text-pretty text-base font-medium leading-6 sm:text-lg sm:leading-7">{headline}</p>
        {definition ? (
          <p className="mt-1.5 text-pretty text-xs text-muted-foreground">{definition}</p>
        ) : null}
      </div>
      <Button variant="outline" size="sm" className="self-start" onClick={copy}>
        <Copy aria-hidden="true" />
        Скопировать
      </Button>
    </section>
  );
}
