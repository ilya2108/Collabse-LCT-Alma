import type { LucideIcon } from 'lucide-react';
import { motion, useReducedMotion } from 'motion/react';
import { useId, type ReactNode } from 'react';
import { cn } from '@/shared/lib/cn';
import { springs } from '@/shared/lib/motion';

/**
 * SegmentedControl (redesign.md §3.10): скользящая подложка layoutId
 * (springs.snappy). Локальная реализация WP2 по замороженному контракту —
 * употребление: тумблер «Ручной приоритет / По заявкам» (§6.5).
 */

export interface SegmentedOption<V extends string = string> {
  value: V;
  label: string;
  icon?: LucideIcon;
  count?: number;
}

export interface SegmentedControlProps<V extends string = string> {
  options: SegmentedOption<V>[];
  value: V;
  onChange: (value: V) => void;
  size?: 'sm' | 'md';
}

export function SegmentedControl<V extends string = string>({
  options,
  value,
  onChange,
  size = 'md',
}: SegmentedControlProps<V>): ReactNode {
  const layoutId = useId();
  const reduced = useReducedMotion() ?? false;
  return (
    <div role="radiogroup" className="inline-flex items-center gap-0.5 rounded-md bg-muted p-0.5">
      {options.map((option) => {
        const active = option.value === value;
        const Icon = option.icon;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(option.value)}
            className={cn(
              'relative inline-flex items-center gap-1.5 whitespace-nowrap rounded-[6px] font-medium transition-colors duration-150',
              size === 'sm' ? 'h-7 px-2.5 text-xs' : 'h-8 px-3 text-sm',
              active ? 'text-foreground' : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {active ? (
              <motion.span
                layoutId={layoutId}
                transition={reduced ? { duration: 0 } : springs.snappy}
                className="absolute inset-0 rounded-[6px] bg-card shadow-card"
                aria-hidden="true"
              />
            ) : null}
            <span className="relative z-10 inline-flex items-center gap-1.5">
              {Icon ? <Icon className="size-3.5" aria-hidden="true" /> : null}
              {option.label}
              {typeof option.count === 'number' ? (
                <span className="tabular text-xs text-muted-foreground">{option.count}</span>
              ) : null}
            </span>
          </button>
        );
      })}
    </div>
  );
}
