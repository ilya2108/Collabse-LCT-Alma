import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { FileText, Loader2, Pencil } from 'lucide-react';
import { useMemo, useState, type ReactNode } from 'react';
import { useForm } from 'react-hook-form';
import { Link, useParams } from 'react-router-dom';
import { z } from 'zod';
import { FileListPanel } from '@/components/files/FileListPanel';
import {
  deleteContractFile,
  getContract,
  listContractFiles,
  updateContract,
  uploadContractFile,
  type ContractPayload,
} from '@/shared/api/endpoints/contracts';
import { listActiveProducts } from '@/shared/api/endpoints/products';
import { listRequests } from '@/shared/api/endpoints/requests';
import type { Contract } from '@/shared/api/types';
import { useAuth } from '@/shared/auth/AuthContext';
import { formatDate, formatMoney, formatText } from '@/shared/lib/format';
import { toastError, toastSuccess } from '@/shared/lib/toast';
import { Badge } from '@/shared/ui/badge';
import { Button } from '@/shared/ui/button';
import {
  ContractStatusTag,
  DatePickerField,
  DateRangePicker,
  DL,
  DLRow,
  ErrorState,
  LoadingState,
  MiniCard,
  PageHeader,
  ProductMultiCombobox,
  SectionCard,
  StageTag,
  StaggerGrid,
  StaggerItem,
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
 * Карточка договора (ux.md §9.2, redesign.md §6.5): DL-реквизиты, файлы
 * версий из MinIO, мини-карточки связанных заявок.
 */

const CONTRACT_STATUS_OPTIONS = [
  { value: 'draft', label: 'Черновик' },
  { value: 'negotiation', label: 'Переговоры' },
  { value: 'active', label: 'Действует' },
  { value: 'completed', label: 'Завершён' },
  { value: 'terminated', label: 'Расторгнут' },
];

const editSchema = z.object({
  number: z.string().trim().min(1, 'Укажите номер'),
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

type EditFormValues = z.infer<typeof editSchema>;

function EditContractSheet({
  contract,
  open,
  onClose,
}: {
  contract: Contract;
  open: boolean;
  onClose: () => void;
}): ReactNode {
  const queryClient = useQueryClient();
  const form = useForm<EditFormValues>({
    resolver: zodResolver(editSchema),
    mode: 'onTouched',
    values: {
      number: contract.number,
      status: contract.status,
      signed_at: contract.signed_at,
      valid_from: contract.valid_from,
      valid_to: contract.valid_to,
      amount: contract.amount != null ? String(Number(contract.amount)) : '',
      product_ids: contract.product_ids,
      notes: contract.notes ?? '',
    },
  });
  const mutation = useMutation({
    mutationFn: (patch: Partial<ContractPayload> & { version: number }) =>
      updateContract(contract.id, patch),
    onSuccess: () => {
      toastSuccess('Договор сохранён');
      void queryClient.invalidateQueries({ queryKey: ['contract', contract.id] });
      void queryClient.invalidateQueries({ queryKey: ['registry', 'registry.contracts'] });
      onClose();
    },
    onError: (error) => toastError(error, { title: 'Не удалось сохранить договор' }),
  });

  return (
    <Sheet open={open} onOpenChange={(next) => (next ? undefined : onClose())}>
      <SheetContent side="right" className="sm:max-w-[480px]">
        <SheetHeader>
          <SheetTitle>Договор {contract.number}</SheetTitle>
          <SheetDescription>Статус, срок действия, сумма и продукты</SheetDescription>
        </SheetHeader>
        <Form {...form}>
          <form
            className="flex flex-col gap-4 overflow-y-auto px-4 pb-4"
            onSubmit={form.handleSubmit((values) =>
              mutation.mutate({
                version: contract.version,
                number: values.number.trim(),
                status: values.status,
                signed_at: values.signed_at,
                valid_from: values.valid_from,
                valid_to: values.valid_to,
                amount: values.amount ? Number(values.amount.replace(',', '.')).toFixed(2) : null,
                product_ids: values.product_ids,
                notes: values.notes?.trim() || null,
              }),
            )}
          >
            <FormField
              control={form.control}
              name="number"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Номер</FormLabel>
                  <FormControl>
                    <Input autoComplete="off" spellCheck={false} {...field} />
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
                    <Input inputMode="numeric" placeholder="1 200 000…" {...field} />
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
                {mutation.isPending ? 'Сохраняем…' : 'Сохранить'}
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

export function ContractDetailPage(): ReactNode {
  const { id = '' } = useParams<'id'>();
  const { hasPermission } = useAuth();
  const queryClient = useQueryClient();
  const [editOpen, setEditOpen] = useState(false);
  const canWrite = hasPermission('contracts:write');

  const contractQuery = useQuery({
    queryKey: ['contract', id],
    queryFn: ({ signal }) => getContract(id, signal),
    enabled: Boolean(id),
  });
  const filesQuery = useQuery({
    queryKey: ['contract-files', id],
    queryFn: ({ signal }) => listContractFiles(id, signal),
    enabled: Boolean(id),
  });
  const productsQuery = useQuery({
    queryKey: ['products-select'],
    queryFn: ({ signal }) => listActiveProducts(signal),
    staleTime: 60_000,
  });
  const contract = contractQuery.data;
  // Заявки по договору: фильтра contract_id в GET /requests нет (§4.1) —
  // берём заявки вуза и сужаем на клиенте.
  const requestsQuery = useQuery({
    queryKey: ['contract-requests', id, contract?.university_id],
    queryFn: ({ signal }) =>
      listRequests({ university_id: contract?.university_id ?? undefined }, { limit: 100, signal }),
    enabled: Boolean(contract?.university_id),
  });

  const productNames = useMemo(() => {
    const map = new Map<string, string>();
    for (const product of productsQuery.data?.items ?? []) map.set(product.id, product.name);
    return map;
  }, [productsQuery.data]);

  if (contractQuery.isLoading) return <LoadingState rows={8} />;
  if (contractQuery.isError || !contract) {
    return (
      <ErrorState
        error={contractQuery.error}
        title="Не удалось загрузить договор"
        onRetry={() => void contractQuery.refetch()}
      />
    );
  }

  const relatedRequests = (requestsQuery.data?.items ?? []).filter(
    (request) => request.contract_id === contract.id,
  );

  return (
    <>
      <PageHeader
        title={`Договор ${contract.number}`}
        backTo="/registry/contracts"
        meta={<ContractStatusTag status={contract.status} />}
        extra={
          canWrite ? (
            <Button variant="outline" onClick={() => setEditOpen(true)}>
              <Pencil aria-hidden="true" />
              Редактировать
            </Button>
          ) : undefined
        }
      />
      <StaggerGrid className="grid grid-cols-12 gap-4">
        <div className="col-span-12 flex flex-col gap-4 lg:col-span-7">
          <StaggerItem>
            <SectionCard title="Реквизиты">
              <DL labelWidth={180}>
                <DLRow label="Владелец">
                  {contract.university ? (
                    <Link
                      to={`/registry/universities/${contract.university.id}`}
                      className="text-primary hover:underline"
                    >
                      {contract.university.name}
                    </Link>
                  ) : (
                    formatText(contract.counterparty?.display_name)
                  )}
                </DLRow>
                <DLRow label="Дата подписания">{formatDate(contract.signed_at)}</DLRow>
                <DLRow label="Срок действия">
                  {contract.valid_from || contract.valid_to
                    ? `${formatDate(contract.valid_from)} — ${formatDate(contract.valid_to)}`
                    : '—'}
                </DLRow>
                <DLRow label="Сумма">
                  <span className="tabular">{formatMoney(contract.amount, contract.currency)}</span>
                </DLRow>
                <DLRow label="Продукты">
                  {contract.product_ids.length > 0 ? (
                    <span className="flex flex-wrap gap-1.5">
                      {contract.product_ids.map((productId) => (
                        <Badge key={productId} variant="secondary">
                          {productNames.get(productId) ?? '…'}
                        </Badge>
                      ))}
                    </span>
                  ) : (
                    '—'
                  )}
                </DLRow>
                <DLRow label="Примечание">{formatText(contract.notes)}</DLRow>
              </DL>
            </SectionCard>
          </StaggerItem>
          <StaggerItem>
            <SectionCard title="Файлы договора">
              <FileListPanel
                files={filesQuery.data?.items ?? []}
                loading={filesQuery.isLoading}
                onUpload={
                  canWrite
                    ? async (file) => {
                        await uploadContractFile(contract.id, file);
                        await queryClient.invalidateQueries({ queryKey: ['contract-files', id] });
                      }
                    : null
                }
                onDelete={
                  hasPermission('contracts:write')
                    ? async (fileId) => {
                        await deleteContractFile(contract.id, fileId);
                        await queryClient.invalidateQueries({ queryKey: ['contract-files', id] });
                      }
                    : null
                }
                emptyText="Файлов договора пока нет"
              />
            </SectionCard>
          </StaggerItem>
        </div>
        <div className="col-span-12 lg:col-span-5">
          <StaggerItem>
            <SectionCard title="Заявки по договору">
              {requestsQuery.isLoading ? (
                <LoadingState rows={3} card={false} />
              ) : relatedRequests.length === 0 ? (
                <p className="py-2 text-sm text-muted-foreground">Связанных заявок нет</p>
              ) : (
                <div className="flex flex-col gap-2">
                  {relatedRequests.map((request) => (
                    <MiniCard
                      key={request.id}
                      to={`/requests/${request.id}`}
                      icon={FileText}
                      title={request.title}
                      description={request.assignee?.full_name ?? 'Не назначен'}
                      extra={
                        <StageTag name={request.status.name} color={request.status.color} size="sm" />
                      }
                    />
                  ))}
                </div>
              )}
            </SectionCard>
          </StaggerItem>
        </div>
      </StaggerGrid>
      <EditContractSheet contract={contract} open={editOpen} onClose={() => setEditOpen(false)} />
    </>
  );
}
