import { ChevronLeft, ChevronRight } from 'lucide-react';
import * as React from 'react';
import { DayPicker } from 'react-day-picker';
import { ru } from 'react-day-picker/locale';
import { buttonVariants } from '@/shared/ui/button';
import { cn } from '@/shared/lib/cn';

/**
 * Календарь на react-day-picker v9 (§5.1: замена DatePicker в связке
 * Popover + Calendar). Локаль ru по умолчанию; пресеты периодов —
 * кнопки в футере поповера (на стороне употребления).
 */
export type CalendarProps = React.ComponentProps<typeof DayPicker>;

function Calendar({
  className,
  classNames,
  showOutsideDays = true,
  locale = ru,
  ...props
}: CalendarProps): React.ReactElement {
  return (
    <DayPicker
      showOutsideDays={showOutsideDays}
      locale={locale}
      className={cn('p-3', className)}
      classNames={{
        months: 'flex flex-col sm:flex-row gap-4 relative',
        month: 'flex flex-col gap-4',
        month_caption: 'flex justify-center pt-1 relative items-center h-9',
        caption_label: 'text-sm font-medium capitalize',
        nav: 'absolute inset-x-0 top-0 z-10 flex h-9 items-center justify-between px-1',
        button_previous: cn(
          buttonVariants({ variant: 'ghost', size: 'icon' }),
          'size-7 p-0 text-muted-foreground hover:text-foreground',
        ),
        button_next: cn(
          buttonVariants({ variant: 'ghost', size: 'icon' }),
          'size-7 p-0 text-muted-foreground hover:text-foreground',
        ),
        month_grid: 'w-full border-collapse space-y-1',
        weekdays: 'flex',
        weekday: 'w-9 rounded-md text-[11px] font-semibold uppercase tracking-wide text-muted-foreground',
        week: 'mt-2 flex w-full',
        day: cn(
          'relative size-9 p-0 text-center text-sm focus-within:relative focus-within:z-20',
          '[&:has([aria-selected])]:bg-primary-tint [&:first-child:has([aria-selected])]:rounded-l-md [&:last-child:has([aria-selected])]:rounded-r-md',
        ),
        day_button: cn(
          buttonVariants({ variant: 'ghost' }),
          'size-9 p-0 font-normal aria-selected:opacity-100',
        ),
        range_start: 'rounded-l-md',
        range_end: 'rounded-r-md',
        selected:
          '[&>button]:bg-primary [&>button]:text-primary-foreground [&>button]:hover:bg-primary [&>button]:hover:text-primary-foreground',
        today: '[&>button]:bg-accent [&>button]:text-accent-foreground',
        outside: 'text-muted-foreground aria-selected:text-muted-foreground',
        disabled: 'text-muted-foreground opacity-50',
        range_middle: 'aria-selected:bg-primary-tint aria-selected:text-foreground [&>button]:bg-transparent [&>button]:text-foreground',
        hidden: 'invisible',
        ...classNames,
      }}
      components={{
        Chevron: ({ orientation, ...chevronProps }) =>
          orientation === 'left' ? (
            <ChevronLeft className="size-4" aria-hidden="true" {...chevronProps} />
          ) : (
            <ChevronRight className="size-4" aria-hidden="true" {...chevronProps} />
          ),
      }}
      {...props}
    />
  );
}

export { Calendar };
