import type { LucideIcon } from 'lucide-react';
import { motion } from 'motion/react';
import { useId, type ReactNode } from 'react';
import { cn } from '@/shared/lib/cn';
import { springs } from '@/shared/lib/motion';

/**
 * ЛОКАЛЬНЫЙ АДАПТЕР SegmentedControl (redesign.md §3.10) для дашборда:
 * общий компонент за WP-владельцем первого употребления; здесь — локальная
 * реализация того же контракта (options/value/onChange/size) со скользящей
 * подложкой `layoutId` (springs.snappy). На Integrate заменяется общим.
 */

export interface SegmentedOption {
  value: string;
  label: string;
  icon?: LucideIcon;
  count?: number;
}

interface SegmentedProps {
  options: SegmentedOption[];
  value: string;
  onChange: (value: string) => void;
  size?: 'sm' | 'md';
  'aria-label'?: string;
}

export function Segmented({
  options,
  value,
  onChange,
  size = 'md',
  'aria-label': ariaLabel,
}: SegmentedProps): ReactNode {
  const thumbId = useId();
  return (
    <div
      role="group"
      aria-label={ariaLabel}
      className="inline-flex items-center gap-0.5 rounded-md bg-muted p-0.5"
    >
      {options.map((option) => {
        const active = option.value === value;
        const Icon = option.icon;
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(option.value)}
            className={cn(
              'relative inline-flex items-center gap-1.5 rounded-[6px] font-medium outline-none',
              'transition-colors duration-150 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
              size === 'sm' ? 'h-6 px-2.5 text-xs' : 'h-8 px-3 text-sm',
              active ? 'text-foreground' : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {active ? (
              <motion.span
                layoutId={`segment-thumb-${thumbId}`}
                transition={springs.snappy}
                className="absolute inset-0 rounded-[6px] bg-card shadow-card"
                aria-hidden="true"
              />
            ) : null}
            {Icon ? <Icon className="relative z-10 size-3.5" aria-hidden="true" /> : null}
            <span className="relative z-10">{option.label}</span>
            {option.count !== undefined ? (
              <span className="tabular relative z-10 text-xs text-muted-foreground">
                {option.count}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
