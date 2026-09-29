import { Eye, Loader2 } from 'lucide-react';
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import type { ReactNode } from 'react';
import { useAuth } from '@/shared/auth/AuthContext';
import { cn } from '@/shared/lib/cn';
import { motionTokens } from '@/shared/lib/motion';
import { Button } from '@/shared/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/ui/tooltip';
import type { RevealController } from './useReveal';

/**
 * Ячейки ПДн (redesign.md §6.7, ux.md §12.2): маскированные значения —
 * моноширинная маска text-muted-foreground; кнопка Eye (kam+) с тултипом
 * об аудите; раскрытое значение — подсветка talent-tint и таймер-точка 60 с
 * (анимация масштаба; reduce — статичная), затем обратная маскировка fade.
 */

interface RevealEyeProps {
  studentId: string;
  controller: RevealController;
}

/** Кнопка раскрытия + таймер-точка: одна на строку/карточку. */
export function RevealEye({ studentId, controller }: RevealEyeProps): ReactNode {
  const { hasRole } = useAuth();
  const reduced = useReducedMotion() ?? false;
  if (!hasRole('admin', 'head_kam', 'kam')) return null;

  const seconds = controller.secondsLeft(studentId);
  if (seconds > 0) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex items-center gap-1 text-xs text-status-talent-deep tabular">
            <motion.span
              className="size-1.5 rounded-full bg-status-talent"
              aria-hidden="true"
              animate={reduced ? undefined : { scale: [1, 1.5, 1] }}
              transition={
                reduced
                  ? undefined
                  : { duration: motionTokens.duration.slow * 2, repeat: Infinity, ease: 'easeInOut' }
              }
            />
            {seconds} с
          </span>
        </TooltipTrigger>
        <TooltipContent>Данные снова будут скрыты по истечении таймера</TooltipContent>
      </Tooltip>
    );
  }

  const revealing = controller.isRevealing(studentId);
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="size-7 text-muted-foreground hover:text-foreground"
          aria-label="Показать ПДн"
          disabled={revealing}
          onClick={(e) => {
            e.stopPropagation();
            controller.reveal(studentId);
          }}
        >
          {revealing ? (
            <Loader2 className="animate-spin" aria-hidden="true" />
          ) : (
            <Eye aria-hidden="true" />
          )}
        </Button>
      </TooltipTrigger>
      <TooltipContent>Просмотр будет зафиксирован в журнале аудита</TooltipContent>
    </Tooltip>
  );
}

interface PiiValueProps {
  studentId: string;
  controller: RevealController;
  masked: string;
  field: 'full_name' | 'email' | 'phone';
  strong?: boolean;
}

/** Значение ПДн: маскированное или раскрытое (подсветка + fade назад). */
export function PiiValue({ studentId, controller, masked, field, strong }: PiiValueProps): ReactNode {
  const revealed = controller.revealed(studentId);
  const value = revealed ? (revealed[field] ?? masked) : masked;
  return (
    <AnimatePresence mode="wait" initial={false}>
      <motion.span
        key={revealed ? 'revealed' : 'masked'}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: motionTokens.duration.fast }}
        className={cn(
          'inline-block max-w-full truncate align-bottom',
          strong && 'font-medium',
          revealed
            ? 'rounded-sm bg-status-talent-tint px-1 text-status-talent-deep'
            : 'font-mono text-muted-foreground',
        )}
      >
        {value || '—'}
      </motion.span>
    </AnimatePresence>
  );
}
