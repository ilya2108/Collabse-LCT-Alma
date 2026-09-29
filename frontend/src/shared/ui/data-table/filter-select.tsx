import type { ReactNode } from 'react';
import { cn } from '@/shared/lib/cn';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/ui/select';

/**
 * Селект-фильтр тулбара DataTable: очищаемый (пункт «Все»), значение —
 * string | undefined, как ждут RegistryFilters.
 */

const ALL = '__all__';

export interface FilterSelectOption {
  value: string;
  label: string;
}

export interface FilterSelectProps {
  options: FilterSelectOption[];
  value: string | undefined;
  onChange: (value: string | undefined) => void;
  placeholder: string;
  /** Подпись пункта «все» (по умолчанию — placeholder: любой). */
  allLabel?: string;
  className?: string;
}

export function FilterSelect({
  options,
  value,
  onChange,
  placeholder,
  allLabel,
  className,
}: FilterSelectProps): ReactNode {
  return (
    <Select
      value={value ?? ALL}
      onValueChange={(next) => onChange(next === ALL ? undefined : next)}
    >
      <SelectTrigger className={cn('min-w-[140px]', className)} aria-label={placeholder}>
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ALL}>{allLabel ?? `${placeholder}: все`}</SelectItem>
        {options.map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
