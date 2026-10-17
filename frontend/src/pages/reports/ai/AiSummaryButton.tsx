import { LoaderCircle, Sparkles } from 'lucide-react';
import type { ReactNode } from 'react';
import { cn } from '@/shared/lib/cn';
import { Button } from '@/shared/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/ui/tooltip';
import type { AiSummaryController } from './useAiSummary';

/**
 * Кнопка «Сводка ИИ» (ux.md §6.4). Скрыта без права `reports:ai_summary`, при выключенном
 * флаге `ai_summaries` и после ответа 403/422. Пока сводка формируется — спиннер; клик по
 * открытой актуальной сводке не запускает новую, а возвращает к панели.
 */

interface AiSummaryButtonProps {
  controller: AiSummaryController;
  size?: 'default' | 'sm';
  className?: string;
}

export function AiSummaryButton({ controller, size = 'default', className }: AiSummaryButtonProps): ReactNode {
  if (!controller.available) return null;
  const { open, busy, stale, job, requestError, pollExpired } = controller;
  const showing = open && !stale && requestError == null && !pollExpired && (busy || job?.status === 'done');

  const onClick = (): void => {
    if (showing) controller.reveal();
    else controller.start({ mode: job?.mode ?? 'auto' });
  };

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="outline"
          size={size}
          className={cn('shrink-0', className)}
          onClick={onClick}
          disabled={controller.creating}
          aria-busy={busy || undefined}
        >
          {busy ? (
            <LoaderCircle className="animate-spin" aria-hidden="true" />
          ) : (
            <Sparkles className="text-primary" aria-hidden="true" />
          )}
          Сводка ИИ
        </Button>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">
        Короткая сводка фактов по цифрам отчёта. GigaChat получает только цифры, названия этапов и
        направлений и псевдонимы КАМов — без ФИО, вузов и клиентов.
      </TooltipContent>
    </Tooltip>
  );
}
