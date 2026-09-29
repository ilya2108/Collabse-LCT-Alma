import { Check, MoreHorizontal } from 'lucide-react';
import { motion } from 'motion/react';
import type { ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { Illustration } from '@/shared/illustrations';
import { cn } from '@/shared/lib/cn';
import { springs } from '@/shared/lib/motion';
import { Button } from '@/shared/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/shared/ui/dropdown-menu';
import { Progress } from '@/shared/ui/progress';
import { useOnboarding } from './OnboardingProvider';

/**
 * Чек-лист «Начало работы» (redesign.md §7.5): карточка на дашборде
 * (первая позиция сетки, пока не завершён; вставляет стадия Integrate).
 * Пункты отмечаются автоматически хуками экранов (completeChecklistItem),
 * клик по строке ведёт в нужный экран, «Скрыть» — навсегда (в state).
 * На 100% — поздравление с иллюстрацией.
 */

export function ChecklistCard({ className }: { className?: string }): ReactNode {
  const navigate = useNavigate();
  const { ready, checklistItems, state, startTour, config, hideChecklist } = useOnboarding();

  if (!ready || !config || checklistItems.length === 0 || state.checklist_hidden) return null;

  const doneCount = checklistItems.filter((item) => state.checklist[item.id]).length;
  const total = checklistItems.length;
  const completed = doneCount === total;

  if (completed) {
    return (
      <section
        className={cn(
          'flex flex-col items-center gap-2 rounded-lg border bg-card p-5 text-center shadow-card',
          className,
        )}
        aria-label="Начало работы: завершено"
      >
        <Illustration name="onboarding-welcome" height={120} />
        <h2 className="text-base font-semibold">Отличный старт!</h2>
        <p className="text-sm text-muted-foreground">Все шаги контрольного списка выполнены.</p>
        <Button variant="outline" size="sm" onClick={hideChecklist}>
          Скрыть
        </Button>
      </section>
    );
  }

  return (
    <section
      className={cn('rounded-lg border bg-card p-5 shadow-card', className)}
      aria-label="Начало работы"
    >
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-base font-semibold">Начало работы</h2>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="size-7 text-muted-foreground"
              aria-label="Действия с контрольным списком"
            >
              <MoreHorizontal aria-hidden="true" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onSelect={hideChecklist}>Скрыть</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      <div className="mb-4 flex items-center gap-3">
        <Progress value={(doneCount / total) * 100} className="h-1.5" />
        <span className="shrink-0 text-xs text-muted-foreground tabular">
          {doneCount} из {total}
        </span>
      </div>
      <ul className="space-y-1">
        {checklistItems.map((item) => {
          const done = Boolean(state.checklist[item.id]);
          return (
            <li key={item.id}>
              <button
                type="button"
                onClick={() => {
                  if (item.startsTour) startTour(config.welcome.mainTourId);
                  else if (item.route) navigate(item.route);
                }}
                className={cn(
                  'flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left text-sm transition-colors duration-150 hover:bg-accent',
                  done && 'text-muted-foreground line-through decoration-border',
                )}
              >
                <span
                  className={cn(
                    'flex size-5 shrink-0 items-center justify-center rounded-full border',
                    done
                      ? 'border-status-success bg-status-success-tint text-status-success-deep'
                      : 'border-border bg-card',
                  )}
                  aria-hidden="true"
                >
                  {done ? (
                    <motion.span
                      initial={{ scale: 0 }}
                      animate={{ scale: 1 }}
                      transition={springs.bouncy}
                      className="flex"
                    >
                      <Check className="size-3" aria-hidden="true" />
                    </motion.span>
                  ) : null}
                </span>
                {item.title}
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
