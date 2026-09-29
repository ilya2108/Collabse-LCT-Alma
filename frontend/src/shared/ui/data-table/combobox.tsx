import { useQuery } from '@tanstack/react-query';
import { Check, ChevronDown, X } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { searchUniversities } from '@/shared/api/endpoints/universities';
import { listActiveProducts } from '@/shared/api/endpoints/products';
import { listUsers } from '@/shared/api/endpoints/users';
import { useAuth } from '@/shared/auth/AuthContext';
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
 * Combobox нового кита (§5.1: Select с поиском — Popover + Command) и
 * entity-обёртки — замена AntD EntitySelects в мигрированных экранах WP2.
 */

export interface ComboboxOption {
  value: string;
  label: string;
  /** Пример/подпись в опции — text-muted-foreground. */
  hint?: string;
}

export interface ComboboxProps {
  options: ComboboxOption[];
  value?: string | null;
  onChange: (value: string | undefined) => void;
  placeholder?: string;
  /** Плейсхолдер строки поиска внутри поповера. */
  searchPlaceholder?: string;
  emptyText?: string;
  loading?: boolean;
  /** Серверный поиск: отключает клиентскую фильтрацию cmdk. */
  onSearch?: (term: string) => void;
  disabled?: boolean;
  allowClear?: boolean;
  className?: string;
  ariaLabel?: string;
}

export function Combobox({
  options,
  value,
  onChange,
  placeholder = 'Выбрать…',
  searchPlaceholder = 'Поиск…',
  emptyText = 'Ничего не найдено',
  loading = false,
  onSearch,
  disabled = false,
  allowClear = true,
  className,
  ariaLabel,
}: ComboboxProps): ReactNode {
  const [open, setOpen] = useState(false);
  const selected = options.find((o) => o.value === value);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          aria-label={ariaLabel ?? placeholder}
          disabled={disabled}
          className={cn('h-9 justify-between gap-2 px-3 font-normal', className)}
        >
          <span className={cn('min-w-0 truncate', !selected && 'text-muted-foreground')}>
            {selected?.label ?? placeholder}
          </span>
          <span className="flex shrink-0 items-center gap-1">
            {allowClear && selected ? (
              <span
                role="button"
                tabIndex={-1}
                aria-label="Очистить"
                className="rounded-sm p-0.5 text-muted-foreground hover:text-foreground"
                onClick={(e) => {
                  e.stopPropagation();
                  onChange(undefined);
                }}
              >
                <X className="size-3.5" aria-hidden="true" />
              </span>
            ) : null}
            <ChevronDown className="size-4 opacity-50" aria-hidden="true" />
          </span>
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[--radix-popover-trigger-width] min-w-[220px] p-0" align="start">
        <Command shouldFilter={!onSearch}>
          <CommandInput placeholder={searchPlaceholder} onValueChange={onSearch} />
          <CommandList>
            <CommandEmpty>{loading ? 'Загрузка…' : emptyText}</CommandEmpty>
            <CommandGroup>
              {options.map((option) => (
                <CommandItem
                  key={option.value}
                  value={onSearch ? option.value : option.label}
                  onSelect={() => {
                    onChange(option.value === value ? undefined : option.value);
                    setOpen(false);
                  }}
                >
                  <Check
                    className={cn('size-4', option.value === value ? 'opacity-100' : 'opacity-0')}
                    aria-hidden="true"
                  />
                  <span className="min-w-0 flex-1 truncate">{option.label}</span>
                  {option.hint ? (
                    <span className="ml-2 truncate text-xs text-muted-foreground">{option.hint}</span>
                  ) : null}
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

/**
 * Мультивыбор (§5.1: чипы выбранного внутри триггера, счётчик «+N»).
 */
export interface MultiComboboxProps {
  options: ComboboxOption[];
  value: string[];
  onChange: (value: string[]) => void;
  placeholder?: string;
  searchPlaceholder?: string;
  emptyText?: string;
  loading?: boolean;
  disabled?: boolean;
  className?: string;
  ariaLabel?: string;
}

export function MultiCombobox({
  options,
  value,
  onChange,
  placeholder = 'Выбрать…',
  searchPlaceholder = 'Поиск…',
  emptyText = 'Ничего не найдено',
  loading = false,
  disabled = false,
  className,
  ariaLabel,
}: MultiComboboxProps): ReactNode {
  const [open, setOpen] = useState(false);
  const selected = options.filter((o) => value.includes(o.value));
  const shown = selected.slice(0, 2);
  const rest = selected.length - shown.length;
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          aria-label={ariaLabel ?? placeholder}
          disabled={disabled}
          className={cn('h-auto min-h-9 justify-between gap-2 px-3 py-1.5 font-normal', className)}
        >
          {selected.length === 0 ? (
            <span className="truncate text-muted-foreground">{placeholder}</span>
          ) : (
            <span className="flex min-w-0 flex-wrap items-center gap-1">
              {shown.map((option) => (
                <span
                  key={option.value}
                  className="inline-flex max-w-[140px] items-center rounded-sm bg-primary-tint px-1.5 py-0.5 text-xs text-primary"
                >
                  <span className="truncate">{option.label}</span>
                </span>
              ))}
              {rest > 0 ? <span className="text-xs text-muted-foreground">+{rest}</span> : null}
            </span>
          )}
          <ChevronDown className="size-4 shrink-0 opacity-50" aria-hidden="true" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[--radix-popover-trigger-width] min-w-[220px] p-0" align="start">
        <Command>
          <CommandInput placeholder={searchPlaceholder} />
          <CommandList>
            <CommandEmpty>{loading ? 'Загрузка…' : emptyText}</CommandEmpty>
            <CommandGroup>
              {options.map((option) => {
                const checked = value.includes(option.value);
                return (
                  <CommandItem
                    key={option.value}
                    value={option.label}
                    onSelect={() => {
                      onChange(
                        checked ? value.filter((v) => v !== option.value) : [...value, option.value],
                      );
                    }}
                  >
                    <Check className={cn('size-4', checked ? 'opacity-100' : 'opacity-0')} aria-hidden="true" />
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

// --- Entity-обёртки (данные — те же endpoints, что у AntD EntitySelects) -----

interface EntityComboboxProps {
  value?: string | null;
  onChange: (value: string | undefined) => void;
  placeholder?: string;
  className?: string;
  disabled?: boolean;
}

/** Вузы — серверный поиск (их много). */
export function UniversityCombobox({ value, onChange, placeholder = 'Вуз', className, disabled }: EntityComboboxProps): ReactNode {
  const [search, setSearch] = useState('');
  const debounced = useDebouncedValue(search, 300);
  const query = useQuery({
    queryKey: ['universities-select', debounced],
    queryFn: ({ signal }) => searchUniversities(debounced, signal),
    staleTime: 30_000,
  });
  return (
    <Combobox
      options={(query.data?.items ?? []).map((u) => ({ value: u.id, label: u.name }))}
      value={value}
      onChange={onChange}
      onSearch={setSearch}
      placeholder={placeholder}
      searchPlaceholder="Название вуза…"
      emptyText="Вузы не найдены"
      loading={query.isLoading}
      className={className}
      disabled={disabled}
    />
  );
}

/** Продукты, мультивыбор (форма договора). */
export function ProductMultiCombobox({
  value,
  onChange,
  placeholder = 'Продукты по договору',
  className,
  disabled,
}: {
  value: string[];
  onChange: (value: string[]) => void;
  placeholder?: string;
  className?: string;
  disabled?: boolean;
}): ReactNode {
  const query = useQuery({
    queryKey: ['products-select'],
    queryFn: ({ signal }) => listActiveProducts(signal),
    staleTime: 60_000,
  });
  return (
    <MultiCombobox
      options={(query.data?.items ?? []).map((p) => ({ value: p.id, label: p.name }))}
      value={value}
      onChange={onChange}
      placeholder={placeholder}
      searchPlaceholder="Название продукта…"
      emptyText="Продукты не найдены"
      loading={query.isLoading}
      className={className}
      disabled={disabled}
    />
  );
}

export function ProductCombobox({ value, onChange, placeholder = 'Продукт', className, disabled }: EntityComboboxProps): ReactNode {
  const query = useQuery({
    queryKey: ['products-select'],
    queryFn: ({ signal }) => listActiveProducts(signal),
    staleTime: 60_000,
  });
  return (
    <Combobox
      options={(query.data?.items ?? []).map((p) => ({ value: p.id, label: p.name }))}
      value={value}
      onChange={onChange}
      placeholder={placeholder}
      searchPlaceholder="Название продукта…"
      emptyText="Продукты не найдены"
      loading={query.isLoading}
      className={className}
      disabled={disabled}
    />
  );
}

interface UserComboboxProps extends EntityComboboxProps {
  /** Фильтр по роли Keycloak (например, 'kam'). */
  role?: string;
}

/** Пользователь (КАМ/ответственный): GET /admin/users — только admin/head_kam. */
export function UserCombobox({ role, value, onChange, placeholder = 'Пользователь', className, disabled }: UserComboboxProps): ReactNode {
  const { hasRole } = useAuth();
  const allowed = hasRole('admin', 'head_kam');
  const query = useQuery({
    queryKey: ['users-select', role ?? 'all'],
    queryFn: ({ signal }) => listUsers({ role, signal }),
    enabled: allowed,
    staleTime: 60_000,
  });
  return (
    <Combobox
      options={(query.data?.items ?? []).map((u) => ({ value: u.id, label: u.full_name }))}
      value={value}
      onChange={onChange}
      placeholder={placeholder}
      searchPlaceholder="Имя пользователя…"
      emptyText="Пользователи не найдены"
      loading={query.isLoading}
      className={className}
      disabled={disabled}
    />
  );
}
