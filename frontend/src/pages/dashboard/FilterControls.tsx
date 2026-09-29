import { CalendarDays, Check, ChevronDown, X } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import type { DateRange } from 'react-day-picker';
import { cn } from '@/shared/lib/cn';
import { dayjs } from '@/shared/lib/dayjs';
import { Button } from '@/shared/ui/button';
import { Calendar } from '@/shared/ui/calendar';
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/shared/ui/command';
import { Popover, PopoverContent, PopoverTrigger } from '@/shared/ui/popover';

/**
 * Контролы панели фильтров дашборда (redesign.md §6.3, §5.1):
 * — RangePicker → Popover + Calendar (react-day-picker, locale ru) с
 *   пресетами периодов «Месяц/Квартал/Год» в футере поповера;
 * — мультиселекты КАМ/вуз/продукт → Popover + Command (паттерн Combobox)
 *   с чипом выбранного и счётчиком «+N» в триггере.
 */

const DATE_FMT = 'YYYY-MM-DD';

interface DateRangeFilterProps {
  from: string | null;
  to: string | null;
  onChange: (from: string | null, to: string | null) => void;
}

const RANGE_PRESETS = [
  { label: 'Месяц', months: 1 },
  { label: 'Квартал', months: 3 },
  { label: 'Год', months: 12 },
] as const;

export function DateRangeFilter({ from, to, onChange }: DateRangeFilterProps): ReactNode {
  const [open, setOpen] = useState(false);

  const selected: DateRange | undefined =
    from || to
      ? {
          from: from ? dayjs(from).toDate() : undefined,
          to: to ? dayjs(to).toDate() : undefined,
        }
      : undefined;

  const label =
    from || to
      ? `${from ? dayjs(from).format('DD.MM.YYYY') : '…'} — ${
          to ? dayjs(to).format('DD.MM.YYYY') : '…'
        }`
      : 'Весь период';

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="outline" className={cn('font-normal', !from && !to && 'text-muted-foreground')}>
          <CalendarDays aria-hidden="true" />
          <span className="tabular">{label}</span>
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-auto p-0">
        <Calendar
          mode="range"
          numberOfMonths={2}
          defaultMonth={selected?.from ?? dayjs().subtract(1, 'month').toDate()}
          selected={selected}
          onSelect={(range) =>
            onChange(
              range?.from ? dayjs(range.from).format(DATE_FMT) : null,
              range?.to ? dayjs(range.to).format(DATE_FMT) : null,
            )
          }
        />
        <div className="flex items-center gap-1.5 border-t border-border p-2">
          {RANGE_PRESETS.map((preset) => (
            <Button
              key={preset.label}
              variant="secondary"
              size="sm"
              onClick={() => {
                onChange(
                  dayjs().subtract(preset.months, 'month').format(DATE_FMT),
                  dayjs().format(DATE_FMT),
                );
                setOpen(false);
              }}
            >
              {preset.label}
            </Button>
          ))}
          <Button
            variant="ghost"
            size="sm"
            className="ml-auto"
            onClick={() => {
              onChange(null, null);
              setOpen(false);
            }}
          >
            Весь период
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}

export interface MultiFilterOption {
  value: string;
  label: string;
}

interface MultiFilterProps {
  placeholder: string;
  options: MultiFilterOption[];
  value: string[];
  onChange: (value: string[]) => void;
  /** Серверный поиск (вузы): выключает клиентский фильтр cmdk. */
  onSearch?: (query: string) => void;
  loading?: boolean;
  emptyText?: string;
}

export function MultiFilter({
  placeholder,
  options,
  value,
  onChange,
  onSearch,
  loading = false,
  emptyText = 'Ничего не найдено',
}: MultiFilterProps): ReactNode {
  const [open, setOpen] = useState(false);

  const selectedLabels = value.map(
    (v) => options.find((o) => o.value === v)?.label ?? v,
  );

  const toggle = (optionValue: string): void => {
    onChange(
      value.includes(optionValue)
        ? value.filter((v) => v !== optionValue)
        : [...value, optionValue],
    );
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          aria-label={value.length > 0 ? `${placeholder}: выбрано ${value.length}` : placeholder}
          className={cn('max-w-56 justify-between font-normal', value.length === 0 && 'text-muted-foreground')}
        >
          <span className="min-w-0 truncate">
            {value.length === 0 ? placeholder : selectedLabels[0]}
          </span>
          <span className="flex shrink-0 items-center gap-1">
            {value.length > 1 ? (
              <span className="tabular rounded-sm bg-primary-tint-2 px-1.5 py-0.5 text-xs font-medium text-primary-active">
                +{value.length - 1}
              </span>
            ) : null}
            {value.length > 0 ? (
              <span
                role="button"
                tabIndex={0}
                aria-label={`Очистить фильтр «${placeholder}»`}
                className="rounded-sm p-0.5 text-muted-foreground hover:text-foreground"
                onClick={(event) => {
                  event.stopPropagation();
                  onChange([]);
                }}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    event.stopPropagation();
                    onChange([]);
                  }
                }}
              >
                <X className="size-3.5" aria-hidden="true" />
              </span>
            ) : (
              <ChevronDown className="size-4 text-muted-foreground" aria-hidden="true" />
            )}
          </span>
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-64 p-0">
        <Command shouldFilter={!onSearch}>
          <CommandInput placeholder="Найти…" onValueChange={onSearch} />
          <CommandList>
            <CommandEmpty>{loading ? 'Загрузка…' : emptyText}</CommandEmpty>
            <CommandGroup>
              {options.map((option) => {
                const active = value.includes(option.value);
                return (
                  <CommandItem
                    key={option.value}
                    value={onSearch ? option.value : option.label}
                    onSelect={() => toggle(option.value)}
                  >
                    <span
                      aria-hidden="true"
                      className={cn(
                        'flex size-4 items-center justify-center rounded-sm border',
                        active
                          ? 'border-primary bg-primary text-primary-foreground'
                          : 'border-input',
                      )}
                    >
                      {active ? <Check className="size-3" /> : null}
                    </span>
                    <span className="min-w-0 flex-1 truncate">{option.label}</span>
                  </CommandItem>
                );
              })}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
