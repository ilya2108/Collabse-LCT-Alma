import * as React from 'react';
import { cn } from '@/shared/lib/cn';

/**
 * Скелетон с shimmer-переливом (§2.3): класс .shimmer из globals.css,
 * при prefers-reduced-motion перелив статичен. Скелетон повторяет каркас экрана.
 */
function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>): React.ReactElement {
  return (
    <div
      data-slot="skeleton"
      aria-hidden="true"
      className={cn('shimmer rounded-md bg-muted', className)}
      {...props}
    />
  );
}

export { Skeleton };
