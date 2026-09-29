import { useQuery } from '@tanstack/react-query';
import { useState, type CSSProperties, type ReactNode } from 'react';
import { listContractsByUniversity } from '@/shared/api/endpoints/contracts';
import { listInteractionTypes } from '@/shared/api/endpoints/interactionTypes';
import { listProgramsByProduct, listPrograms } from '@/shared/api/endpoints/programs';
import { listActiveProducts } from '@/shared/api/endpoints/products';
import { searchUniversities } from '@/shared/api/endpoints/universities';
import { listUsers } from '@/shared/api/endpoints/users';
import { useAuth } from '@/shared/auth/AuthContext';
import { useDebouncedValue } from '@/shared/lib/useDebouncedValue';
import { Combobox, type ComboboxOption } from '@/shared/ui/data-table';

/**
 * Селекты-справочники для форм и фильтров на новом ките (§5.1:
 * Popover + Command). Каждый — тонкая обёртка над useQuery + Combobox:
 * вузы ищутся на сервере (их много), остальные справочники грузятся целиком
 * и фильтруются на клиенте. Query-ключи прежние — общий кэш со старым кодом.
 */

interface BaseProps {
  value?: string | null;
  onChange?: (value: string | undefined) => void;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  allowClear?: boolean;
  /** Внешний индикатор занятости (например, мутация назначения). */
  loading?: boolean;
  style?: CSSProperties;
  /**
   * Совместимость с прежним AntD-API: Radix-поповеры позиционируются в
   * портале сами, контейнер не нужен. Проп принимается и игнорируется.
   */
  getPopupContainer?: unknown;
  /** Совместимость с прежним AntD-API: размер триггера не варьируется. */
  size?: 'small' | 'middle' | 'large';
}

interface QuerySelectProps extends BaseProps {
  options: ComboboxOption[];
  isLoading: boolean;
  searchPlaceholder: string;
  emptyText: string;
  onSearch?: (term: string) => void;
}

function QuerySelect({
  options,
  isLoading,
  searchPlaceholder,
  emptyText,
  onSearch,
  value,
  onChange,
  placeholder,
  disabled,
  className,
  allowClear = true,
  loading = false,
  style,
}: QuerySelectProps): ReactNode {
  const combobox = (
    <Combobox
      options={options}
      value={value}
      onChange={(next) => onChange?.(next)}
      placeholder={placeholder}
      searchPlaceholder={searchPlaceholder}
      emptyText={emptyText}
      loading={isLoading || loading}
      onSearch={onSearch}
      disabled={disabled || loading}
      allowClear={allowClear}
      className={className}
    />
  );
  return style ? <span style={{ display: 'inline-flex', ...style }}>{combobox}</span> : combobox;
}

export function UniversitySelect(props: BaseProps): ReactNode {
  const [search, setSearch] = useState('');
  const debounced = useDebouncedValue(search, 300);
  const query = useQuery({
    queryKey: ['universities-select', debounced],
    queryFn: ({ signal }) => searchUniversities(debounced, signal),
    staleTime: 30_000,
  });
  return (
    <QuerySelect
      placeholder="Вуз"
      {...props}
      options={(query.data?.items ?? []).map((u) => ({ value: u.id, label: u.name }))}
      isLoading={query.isLoading}
      onSearch={setSearch}
      searchPlaceholder="Название вуза…"
      emptyText="Вузы не найдены"
    />
  );
}

export function ProductSelect(props: BaseProps): ReactNode {
  const query = useQuery({
    queryKey: ['products-select'],
    queryFn: ({ signal }) => listActiveProducts(signal),
    staleTime: 60_000,
  });
  return (
    <QuerySelect
      placeholder="Продукт"
      {...props}
      options={(query.data?.items ?? []).map((p) => ({ value: p.id, label: p.name }))}
      isLoading={query.isLoading}
      searchPlaceholder="Название продукта…"
      emptyText="Продукты не найдены"
    />
  );
}

interface ProgramSelectProps extends BaseProps {
  /** Сужение по продукту (форма заявки). */
  productId?: string | null;
}

export function ProgramSelect({ productId, ...props }: ProgramSelectProps): ReactNode {
  const query = useQuery({
    queryKey: ['programs-select', productId ?? 'all'],
    queryFn: ({ signal }) =>
      productId
        ? listProgramsByProduct(productId, signal)
        : listPrograms({ limit: 200, offset: 0, filters: { is_active: 'true' }, signal }),
    staleTime: 60_000,
  });
  return (
    <QuerySelect
      placeholder="Программа"
      {...props}
      options={(query.data?.items ?? []).map((p) => ({ value: p.id, label: p.name }))}
      isLoading={query.isLoading}
      searchPlaceholder="Название программы…"
      emptyText="Программы не найдены"
    />
  );
}

interface ContractSelectProps extends BaseProps {
  universityId?: string | null;
}

/** Договоры вуза — для привязки в заявке (ux.md §8.1). */
export function ContractSelect({ universityId, ...props }: ContractSelectProps): ReactNode {
  const query = useQuery({
    queryKey: ['contracts-select', universityId],
    queryFn: ({ signal }) => listContractsByUniversity(universityId as string, signal),
    enabled: Boolean(universityId),
    staleTime: 30_000,
  });
  return (
    <QuerySelect
      placeholder={universityId ? 'Договор' : 'Сначала выберите вуз'}
      {...props}
      disabled={props.disabled || !universityId}
      options={(query.data?.items ?? []).map((c) => ({ value: c.id, label: c.number }))}
      isLoading={query.isLoading}
      searchPlaceholder="Номер договора…"
      emptyText="Договоры не найдены"
    />
  );
}

interface UserSelectProps extends BaseProps {
  /** Фильтр по роли Keycloak (например, 'kam'). */
  role?: string;
}

/**
 * Селект пользователя (ответственный/КАМ). `GET /admin/users` доступен только
 * admin и head_kam (api-contract.md §10.2) — рендерите под <Can>.
 */
export function UserSelect({ role, ...props }: UserSelectProps): ReactNode {
  const { hasRole } = useAuth();
  const allowed = hasRole('admin', 'head_kam');
  const query = useQuery({
    queryKey: ['users-select', role ?? 'all'],
    queryFn: ({ signal }) => listUsers({ role, signal }),
    enabled: allowed,
    staleTime: 60_000,
  });
  return (
    <QuerySelect
      placeholder="Пользователь"
      {...props}
      options={(query.data?.items ?? []).map((u) => ({ value: u.id, label: u.full_name }))}
      isLoading={query.isLoading}
      searchPlaceholder="Имя пользователя…"
      emptyText="Пользователи не найдены"
    />
  );
}

export function InteractionTypeSelect(props: BaseProps): ReactNode {
  const query = useQuery({
    queryKey: ['interaction-types'],
    queryFn: ({ signal }) => listInteractionTypes(signal),
    staleTime: 60_000,
  });
  return (
    <QuerySelect
      placeholder="Тип взаимодействия"
      {...props}
      options={(query.data?.items ?? []).map((t) => ({ value: t.id, label: t.name }))}
      isLoading={query.isLoading}
      searchPlaceholder="Тип…"
      emptyText="Типы не найдены"
    />
  );
}
