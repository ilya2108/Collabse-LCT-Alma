import { Check } from 'lucide-react';
import { motion } from 'motion/react';
import type { ReactNode } from 'react';
import { cn } from '@/shared/lib/cn';
import { motionTokens } from '@/shared/lib/motion';

/**
 * Stepper (redesign.md §3.10) — мастер импорта и другие пошаговые сценарии:
 * кружки-числа → галки Check у пройденных шагов, соединительная линия
 * заливается bg-primary (transform scaleX, duration.normal). Контракт заморожен.
 */
export interface StepperStep {
  id: string;
  title: string;
  hint?: string;
}

export interface StepperProps {
  steps: StepperStep[];
  current: number;
}

export function Stepper({ steps, current }: StepperProps): ReactNode {
  return (
    <ol className="flex w-full items-start gap-2" aria-label="Шаги">
      {steps.map((step, index) => {
        const done = index < current;
        const active = index === current;
        return (
          <li
            key={step.id}
            className={cn('flex items-start gap-2', index < steps.length - 1 && 'flex-1')}
            aria-current={active ? 'step' : undefined}
          >
            <div className="flex items-center gap-2">
              <span
                className={cn(
                  'flex size-7 shrink-0 items-center justify-center rounded-full border text-xs font-semibold transition-[color,background-color,border-color] duration-150',
                  done && 'border-primary bg-primary text-primary-foreground',
                  active && 'border-primary bg-primary-tint text-primary',
                  !done && !active && 'border-border bg-card text-muted-foreground',
                )}
                aria-hidden="true"
              >
                {done ? <Check className="size-4" aria-hidden="true" /> : index + 1}
              </span>
              <span className="min-w-0">
                <span
                  className={cn(
                    'block truncate text-sm',
                    active ? 'font-medium text-foreground' : 'text-muted-foreground',
                  )}
                >
                  {step.title}
                  {done ? <span className="sr-only"> — выполнен</span> : null}
                </span>
                {step.hint ? (
                  <span className="block truncate text-xs text-muted-foreground">{step.hint}</span>
                ) : null}
              </span>
            </div>
            {index < steps.length - 1 ? (
              <span
                className="relative mt-3.5 h-0.5 min-w-6 flex-1 overflow-hidden rounded-full bg-border"
                aria-hidden="true"
              >
                <motion.span
                  className="absolute inset-0 origin-left bg-primary"
                  initial={false}
                  animate={{ scaleX: done ? 1 : 0 }}
                  transition={{
                    duration: motionTokens.duration.normal,
                    ease: motionTokens.easing.smooth,
                  }}
                />
              </span>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
