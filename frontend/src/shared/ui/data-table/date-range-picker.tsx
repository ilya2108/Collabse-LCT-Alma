import { CalendarDays, X } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import type { DateRange } from 'react-day-picker';
import { cn } from '@/shared/lib/cn';
import { dayjs } from '@/shared/lib/dayjs';
import { Button } from '@/shared/ui/button';
import { Calendar } from '@/shared/ui/calendar';
import { Popover, PopoverContent, PopoverTrigger } from '@/shared/ui/popover';

/**
 * RangePicker нового кита (§5.1: Popover + Calendar, react-day-picker v9,
 * locale ru). Значения наружу — ISO-даты YYYY-MM-DD | null.
 */

export interface DateRangeValue {
  from: string | null;
  to: string | null;
}

interface DateRangePickerProps {
  value: DateRangeValue;
  onChange: (value: DateRangeValue) => void;
  placeholder?: string;
  className?: string;
}

function toDate(iso: string | null): Date | undefined {
  if (!iso) return undefined;
  const d = dayjs(iso);
  return d.isValid() ? d.toDate() : undefined;
}

function label(value: DateRangeValue): string | null {
  if (!value.from && !value.to) return null;
  const fmt = (iso: string | null): string => (iso ? dayjs(iso).format('DD.MM.YYYY') : '…');
  return `${fmt(value.from)} — ${fmt(value.to)}`;
}

export function DateRangePicker({
  value,
  onChange,
  placeholder = 'Период',
  className,
}: DateRangePickerProps): ReactNode {
  const [open, setOpen] = useState(false);
  const text = label(value);
  const selected: DateRange | undefined =
    value.from || value.to ? { from: toDate(value.from), to: toDate(value.to) } : undefined;
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          aria-label={placeholder}
          className={cn('h-9 justify-start gap-2 px-3 font-normal', className)}
        >
          <CalendarDays className="size-4 text-muted-foreground" aria-hidden="true" />
          <span className={cn('tabular', !text && 'text-muted-foreground')}>{text ?? placeholder}</span>
          {text ? (
            <span
              role="button"
              tabIndex={-1}
              aria-label="Сбросить период"
              className="ml-auto rounded-sm p-0.5 text-muted-foreground hover:text-foreground"
              onClick={(e) => {
                e.stopPropagation();
                onChange({ from: null, to: null });
              }}
            >
              <X className="size-3.5" aria-hidden="true" />
            </span>
          ) : null}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-auto p-0" align="start">
        <Calendar
          mode="range"
          numberOfMonths={2}
          defaultMonth={toDate(value.from)}
          selected={selected}
          onSelect={(range: DateRange | undefined) => {
            onChange({
              from: range?.from ? dayjs(range.from).format('YYYY-MM-DD') : null,
              to: range?.to ? dayjs(range.to).format('YYYY-MM-DD') : null,
            });
          }}
        />
      </PopoverContent>
    </Popover>
  );
}

/** Одиночная дата (замена DatePicker в формах WP2). */
interface DatePickerProps {
  value: string | null;
  onChange: (value: string | null) => void;
  placeholder?: string;
  className?: string;
}

export function DatePickerField({
  value,
  onChange,
  placeholder = 'Дата',
  className,
}: DatePickerProps): ReactNode {
  const [open, setOpen] = useState(false);
  const text = value ? dayjs(value).format('DD.MM.YYYY') : null;
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          aria-label={placeholder}
          className={cn('h-9 w-full justify-start gap-2 px-3 font-normal', className)}
        >
          <CalendarDays className="size-4 text-muted-foreground" aria-hidden="true" />
          <span className={cn('tabular', !text && 'text-muted-foreground')}>{text ?? placeholder}</span>
          {text ? (
            <span
              role="button"
              tabIndex={-1}
              aria-label="Очистить дату"
              className="ml-auto rounded-sm p-0.5 text-muted-foreground hover:text-foreground"
              onClick={(e) => {
                e.stopPropagation();
                onChange(null);
              }}
            >
              <X className="size-3.5" aria-hidden="true" />
            </span>
          ) : null}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-auto p-0" align="start">
        <Calendar
          mode="single"
          defaultMonth={toDate(value)}
          selected={toDate(value)}
          onSelect={(date: Date | undefined) => {
            onChange(date ? dayjs(date).format('YYYY-MM-DD') : null);
            setOpen(false);
          }}
        />
      </PopoverContent>
    </Popover>
  );
}
