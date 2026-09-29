import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Plus } from 'lucide-react';
import { useMemo, useState, type ReactNode } from 'react';
import { useForm } from 'react-hook-form';
import { Link, useNavigate } from 'react-router-dom';
import { z } from 'zod';
import { createContract, listContracts } from '@/shared/api/endpoints/contracts';
import { listActiveProducts } from '@/shared/api/endpoints/products';
import type { Contract } from '@/shared/api/types';
import { Can } from '@/shared/auth/Can';
import { dayjs } from '@/shared/lib/dayjs';
import { formatDate, formatMoney, formatText } from '@/shared/lib/format';
import { toastError, toastSuccess } from '@/shared/lib/toast';
import { Badge } from '@/shared/ui/badge';
import { Button } from '@/shared/ui/button';
import {
  ContractStatusTag,
  DataTable,
  DatePickerField,
  DateRangePicker,
  FilterSelect,
  PageHeader,
  ProductMultiCombobox,
  StatusBadge,
  UniversityCombobox,
  type DataTableColumn,
} from '@/shared/ui/data-table';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/shared/ui/form';
import { Input } from '@/shared/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/ui/select';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/shared/ui/sheet';
import { Textarea } from '@/shared/ui/textarea';

/**
 * Реестр договоров (ux.md §9.2) на DataTable (§6.5): статусы-бейджи,
 * подсветка истекающих (<30 дней) бейджем «< 30 дней», сумма, продукты.
 */

const CONTRACT_STATUS_OPTIONS = [
  { value: 'draft', label: 'Черновик' },
  { value: 'negotiation', label: 'Переговоры' },
  { value: 'active', label: 'Действует' },
  { value: 'completed', label: 'Завершён' },
  { value: 'terminated', label: 'Расторгнут' },
];

function isExpiringSoon(contract: Contract): boolean {
  if (contract.status !== 'active' || !contract.valid_to) return false;
  const daysLeft = dayjs(contract.valid_to).diff(dayjs(), 'day');
  return daysLeft >= 0 && daysLeft < 30;
}

// --- Создание договора (§5.2) ---------------------------------------------------

const contractSchema = z.object({
  number: z.string().trim().min(1, 'Укажите номер договора'),
  university_id: z.string().min(1, 'Выберите вуз'),
  status: z.enum(['draft', 'negotiation', 'active', 'completed', 'terminated']),
  signed_at: z.string().nullable(),
  valid_from: z.string().nullable(),
  valid_to: z.string().nullable(),
  amount: z
    .string()
    .optional()
    .refine((v) => !v || Number.isFinite(Number(v.replace(',', '.'))), 'Сумма — число'),
  product_ids: z.array(z.string()),
  notes: z.string().optional(),
});

type ContractFormValues = z.infer<typeof contractSchema>;

function CreateContractSheet({ open, onClose }: { open: boolean; onClose: () => void }): ReactNode {
  const queryClient = useQueryClient();
  const form = useForm<ContractFormValues>({
    resolver: zodResolver(contractSchema),
    mode: 'onTouched',
    defaultValues: {
      number: '',
      university_id: '',
      status: 'draft',
      signed_at: null,
      valid_from: null,
      valid_to: null,
      amount: '',
      product_ids: [],
      notes: '',
    },
  });

  const mutation = useMutation({
    mutationFn: (values: ContractFormValues) =>
      createContract({
        number: values.number.trim(),
        university_id: values.university_id,
        status: values.status,
        signed_at: values.signed_at,
        valid_from: values.valid_from,
        valid_to: values.valid_to,
        amount: values.amount ? Number(values.amount.replace(',', '.')).toFixed(2) : null,
        product_ids: values.product_ids,
        notes: values.notes?.trim() || null,
      }),
    onSuccess: (contract) => {
      toastSuccess(`Договор ${contract.number} создан`);
      if (contract.warning_duplicate) {
        toastSuccess(
          'Обратите внимание',
          'У этого вуза уже есть действующий договор — как правило, договор один.',
        );
      }
      void queryClient.invalidateQueries({ queryKey: ['registry', 'registry.contracts'] });
      form.reset();
      onClose();
    },
    onError: (error) => toastError(error, { title: 'Не удалось создать договор' }),
  });

  return (
    <Sheet open={open} onOpenChange={(next) => (next ? undefined : onClose())}>
      <SheetContent side="right" className="sm:max-w-[480px]">
        <SheetHeader>
          <SheetTitle>Новый договор</SheetTitle>
          <SheetDescription>Номер, вуз, срок действия и продукты</SheetDescription>
        </SheetHeader>
        <Form {...form}>
          <form
            className="flex flex-col gap-4 overflow-y-auto px-4 pb-4"
            onSubmit={form.handleSubmit((values) => mutation.mutate(values))}
          >
            <FormField
              control={form.control}
              name="number"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Номер договора</FormLabel>
                  <FormControl>
                    <Input placeholder="Д-2026/041…" autoComplete="off" spellCheck={false} {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="university_id"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Вуз</FormLabel>
                  <FormControl>
                    <UniversityCombobox
                      className="w-full"
                      value={field.value || undefined}
                      onChange={(value) => field.onChange(value ?? '')}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="status"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Статус</FormLabel>
                  <Select value={field.value} onValueChange={field.onChange}>
                    <FormControl>
                      <SelectTrigger className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {CONTRACT_STATUS_OPTIONS.map((option) => (
                        <SelectItem key={option.value} value={option.value}>
                          {option.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="signed_at"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Дата подписания</FormLabel>
                  <FormControl>
                    <DatePickerField value={field.value} onChange={field.onChange} placeholder="Дата подписания" />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormItem>
              <FormLabel>Срок действия</FormLabel>
              <DateRangePicker
                className="w-full"
                placeholder="Срок действия"
                value={{ from: form.watch('valid_from'), to: form.watch('valid_to') }}
                onChange={(range) => {
                  form.setValue('valid_from', range.from);
                  form.setValue('valid_to', range.to);
                }}
              />
            </FormItem>
            <FormField
              control={form.control}
              name="amount"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Сумма, ₽</FormLabel>
                  <FormControl>
                    <Input placeholder="1 200 000…" inputMode="numeric" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="product_ids"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Продукты</FormLabel>
                  <FormControl>
                    <ProductMultiCombobox className="w-full" value={field.value} onChange={field.onChange} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="notes"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Примечание</FormLabel>
                  <FormControl>
                    <Textarea rows={2} {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <div className="mt-2 flex gap-2">
              <Button type="submit" disabled={mutation.isPending}>
                {mutation.isPending ? <Loader2 className="animate-spin" aria-hidden="true" /> : null}
                {mutation.isPending ? 'Сохраняем…' : 'Создать'}
              </Button>
              <Button type="button" variant="outline" onClick={onClose}>
                Отмена
              </Button>
            </div>
          </form>
        </Form>
      </SheetContent>
    </Sheet>
  );
}

export function ContractsPage(): ReactNode {
  const navigate = useNavigate();
  const [createOpen, setCreateOpen] = useState(false);
  const productsQuery = useQuery({
    queryKey: ['products-select'],
    queryFn: ({ signal }) => listActiveProducts(signal),
    staleTime: 60_000,
  });
  const productNames = useMemo(() => {
    const map = new Map<string, string>();
    for (const product of productsQuery.data?.items ?? []) map.set(product.id, product.name);
    return map;
  }, [productsQuery.data]);

  const columns: DataTableColumn<Contract>[] = useMemo(
    () => [
      {
        key: 'number',
        title: 'Номер',
        dataIndex: 'number',
        sorter: true,
        alwaysVisible: true,
        render: (_v, record) => (
          <span className="flex items-center gap-1.5">
            <Link
              to={`/registry/contracts/${record.id}`}
              className="font-medium text-primary hover:underline"
              onClick={(e) => e.stopPropagation()}
            >
              {record.number}
            </Link>
            {isExpiringSoon(record) ? (
              <StatusBadge status="warning" size="sm">
                &lt; 30 дней
              </StatusBadge>
            ) : null}
          </span>
        ),
      },
      {
        key: 'owner',
        title: 'Вуз / Контрагент',
        render: (_v, record) => {
          if (record.university)
            return (
              <Link
                to={`/registry/universities/${record.university.id}`}
                className="text-primary hover:underline"
                onClick={(e) => e.stopPropagation()}
              >
                {record.university.name}
              </Link>
            );
          if (record.counterparty) return record.counterparty.display_name;
          return formatText(null);
        },
      },
      {
        key: 'status',
        title: 'Статус',
        dataIndex: 'status',
        render: (value) => <ContractStatusTag status={value as Contract['status']} />,
      },
      {
        key: 'valid_from',
        title: 'Действует с',
        dataIndex: 'valid_from',
        sorter: true,
        responsive: ['lg'],
        render: (value) => formatDate(value as Contract['valid_from']),
      },
      {
        key: 'valid_to',
        title: 'Действует по',
        dataIndex: 'valid_to',
        sorter: true,
        render: (value, record) =>
          isExpiringSoon(record) ? (
            <span className="font-medium text-status-warning-deep">
              {formatDate(value as Contract['valid_to'])}
            </span>
          ) : (
            formatDate(value as Contract['valid_to'])
          ),
      },
      {
        key: 'amount',
        title: 'Сумма',
        dataIndex: 'amount',
        sorter: true,
        align: 'right',
        render: (value, record) => formatMoney(value as Contract['amount'], record.currency),
      },
      {
        key: 'products',
        title: 'Продукты',
        responsive: ['lg'],
        render: (_v, record) =>
          record.product_ids.length > 0 ? (
            <span className="flex flex-wrap gap-1">
              {record.product_ids.map((id) => (
                <Badge key={id} variant="secondary">
                  {productNames.get(id) ?? '…'}
                </Badge>
              ))}
            </span>
          ) : (
            formatText(null)
          ),
      },
    ],
    [productNames],
  );

  return (
    <>
      <PageHeader
        title="Договоры"
        extra={
          <Can permission="contracts:write">
            <Button onClick={() => setCreateOpen(true)}>
              <Plus aria-hidden="true" />
              Создать договор
            </Button>
          </Can>
        }
      />
      <DataTable<Contract>
        screen="registry.contracts"
        exportEntityType="contracts"
        columns={columns}
        rowKey="id"
        fetcher={listContracts}
        searchPlaceholder="Номер договора"
        defaultSort={{ field: 'valid_to', order: 'asc' }}
        renderFilters={({ filters, setFilter }) => (
          <>
            <FilterSelect
              placeholder="Статус"
              allLabel="Статус: все"
              options={CONTRACT_STATUS_OPTIONS}
              value={filters.status as string | undefined}
              onChange={(value) => setFilter('status', value)}
            />
            <UniversityCombobox
              className="w-[220px]"
              value={(filters.university_id as string | undefined) ?? undefined}
              onChange={(value) => setFilter('university_id', value)}
            />
          </>
        )}
        emptyIllustration="registry-empty"
        emptyTitle="Договоров пока нет"
        emptyDescription="Создайте договор вручную или импортируйте реестр из Excel"
        emptyAction={
          <Can permission="contracts:write">
            <Button onClick={() => setCreateOpen(true)}>
              <Plus aria-hidden="true" />
              Создать договор
            </Button>
          </Can>
        }
        onRowClick={(record) => navigate(`/registry/contracts/${record.id}`)}
      />
      <CreateContractSheet open={createOpen} onClose={() => setCreateOpen(false)} />
    </>
  );
}
