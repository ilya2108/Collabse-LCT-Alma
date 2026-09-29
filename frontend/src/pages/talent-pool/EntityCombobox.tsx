import { useQuery } from '@tanstack/react-query';
import { Check, ChevronsUpDown, X } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { listPrograms } from '@/shared/api/endpoints/programs';
import { searchUniversities } from '@/shared/api/endpoints/universities';
import { cn } from '@/shared/lib/cn';
import { useDebouncedValue } from '@/shared/lib/useDebouncedValue';
import { Button } from '@/shared/ui/button';
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
 * Локальный Combobox-адаптер WP6 (redesign.md §5.1: Select с поиском →
 * Popover + Command). Селекты вуза/программы нового стека для talent pool —
 * те же query-ключи, что у components/selects/EntitySelects (общий кэш);
 * на стадии Integrate заменяется общим Combobox.
 */

export interface ComboboxOption {
  value: string;
  label: string;
}

interface EntityComboboxProps {
  value?: string | null;
  onChange: (value: string | null) => void;
  options: ComboboxOption[];
  placeholder: string;
  loading?: boolean;
  /** Серверный поиск: отключает клиентскую фильтрацию Command. */
  onSearch?: (query: string) => void;
  emptyText?: string;
  className?: string;
  /** aria-label триггера (иконочная семантика фильтров). */
  label?: string;
}

export function EntityCombobox({
  value,
  onChange,
  options,
  placeholder,
  loading,
  onSearch,
  emptyText = 'Ничего не найдено',
  className,
  label,
}: EntityComboboxProps): ReactNode {
  const [open, setOpen] = useState(false);
  const selected = options.find((option) => option.value === value);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          aria-label={label ?? placeholder}
          className={cn('min-w-44 justify-between font-normal', className)}
        >
          <span className={cn('truncate', !selected && 'text-muted-foreground')}>
            {selected?.label ?? placeholder}
          </span>
          <span className="flex items-center gap-0.5">
            {value ? (
              <X
                className="size-3.5 text-muted-foreground hover:text-foreground"
                aria-hidden="true"
                onClick={(e) => {
                  e.stopPropagation();
                  onChange(null);
                }}
              />
            ) : null}
            <ChevronsUpDown className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
          </span>
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-64 p-0" align="start">
        <Command shouldFilter={!onSearch}>
          <CommandInput
            placeholder="Поиск…"
            onValueChange={onSearch}
          />
          <CommandList>
            <CommandEmpty>{loading ? 'Загрузка…' : emptyText}</CommandEmpty>
            <CommandGroup>
              {options.map((option) => (
                <CommandItem
                  key={option.value}
                  value={onSearch ? option.value : option.label}
                  onSelect={() => {
                    onChange(option.value === value ? null : option.value);
                    setOpen(false);
                  }}
                >
                  <Check
                    className={cn('size-4', option.value === value ? 'opacity-100' : 'opacity-0')}
                    aria-hidden="true"
                  />
                  <span className="truncate">{option.label}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

interface EntityFieldProps {
  value?: string | null;
  onChange: (value: string | null) => void;
  className?: string;
}

/** Вуз — серверный поиск (query-ключ общий с EntitySelects). */
export function UniversityCombobox({ value, onChange, className }: EntityFieldProps): ReactNode {
  const [search, setSearch] = useState('');
  const debounced = useDebouncedValue(search, 300);
  const query = useQuery({
    queryKey: ['universities-select', debounced],
    queryFn: ({ signal }) => searchUniversities(debounced, signal),
    staleTime: 30_000,
  });
  return (
    <EntityCombobox
      value={value}
      onChange={onChange}
      placeholder="Вуз"
      loading={query.isLoading}
      onSearch={setSearch}
      emptyText="Вузы не найдены"
      className={className}
      options={(query.data?.items ?? []).map((u) => ({ value: u.id, label: u.name }))}
    />
  );
}

/** Программа — весь справочник, фильтрация на клиенте. */
export function ProgramCombobox({ value, onChange, className }: EntityFieldProps): ReactNode {
  const query = useQuery({
    queryKey: ['programs-select', 'all'],
    queryFn: ({ signal }) =>
      listPrograms({ limit: 200, offset: 0, filters: { is_active: 'true' }, signal }),
    staleTime: 60_000,
  });
  return (
    <EntityCombobox
      value={value}
      onChange={onChange}
      placeholder="Программа"
      loading={query.isLoading}
      emptyText="Программы не найдены"
      className={className}
      options={(query.data?.items ?? []).map((p) => ({ value: p.id, label: p.name }))}
    />
  );
}
