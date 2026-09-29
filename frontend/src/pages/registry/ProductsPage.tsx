import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Loader2, Plus } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { useForm } from 'react-hook-form';
import { Link, useNavigate } from 'react-router-dom';
import { z } from 'zod';
import { createProduct, listProducts, updateProduct } from '@/shared/api/endpoints/products';
import type { Product } from '@/shared/api/types';
import { Can } from '@/shared/auth/Can';
import { useAuth } from '@/shared/auth/AuthContext';
import { formatDate, formatText } from '@/shared/lib/format';
import { toastError, toastSuccess } from '@/shared/lib/toast';
import { Badge } from '@/shared/ui/badge';
import { Button } from '@/shared/ui/button';
import {
  DataTable,
  FilterSelect,
  PageHeader,
  StatusBadge,
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
import { Switch } from '@/shared/ui/switch';
import { Textarea } from '@/shared/ui/textarea';

/** Реестр продуктов (ux.md §9.3) на DataTable: активность — Switch в ячейке (§6.5). */

export const PRODUCT_TYPE_LABELS: Record<string, string> = {
  course: 'Курс',
  dpo_program: 'Программа ДПО',
  network_program: 'Сетевая программа',
  service: 'Услуга',
  other: 'Другое',
};

const PRODUCT_TYPE_OPTIONS = Object.entries(PRODUCT_TYPE_LABELS).map(([value, label]) => ({
  value,
  label,
}));

// --- Создание продукта (§5.2) -------------------------------------------------

const productSchema = z.object({
  name: z.string().trim().min(1, 'Укажите название'),
  code: z.string().trim().min(1, 'Укажите код продукта'),
  product_type: z.string(),
  description: z.string().optional(),
});

type ProductFormValues = z.infer<typeof productSchema>;

function CreateProductSheet({ open, onClose }: { open: boolean; onClose: () => void }): ReactNode {
  const queryClient = useQueryClient();
  const form = useForm<ProductFormValues>({
    resolver: zodResolver(productSchema),
    mode: 'onTouched',
    defaultValues: { name: '', code: '', product_type: 'course', description: '' },
  });
  const mutation = useMutation({
    mutationFn: (values: ProductFormValues) =>
      createProduct({
        name: values.name.trim(),
        code: values.code.trim(),
        product_type: values.product_type,
        description: values.description?.trim() || null,
      }),
    onSuccess: (product) => {
      toastSuccess(`Продукт «${product.name}» создан`);
      void queryClient.invalidateQueries({ queryKey: ['registry', 'registry.products'] });
      void queryClient.invalidateQueries({ queryKey: ['products-select'] });
      form.reset();
      onClose();
    },
    onError: (error) => toastError(error, { title: 'Не удалось создать продукт' }),
  });
  return (
    <Sheet open={open} onOpenChange={(next) => (next ? undefined : onClose())}>
      <SheetContent side="right" className="sm:max-w-[480px]">
        <SheetHeader>
          <SheetTitle>Новый продукт</SheetTitle>
          <SheetDescription>Продукты создаёт руководитель КАМов или администратор</SheetDescription>
        </SheetHeader>
        <Form {...form}>
          <form
            className="flex flex-col gap-4 overflow-y-auto px-4 pb-4"
            onSubmit={form.handleSubmit((values) => mutation.mutate(values))}
          >
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Название</FormLabel>
                  <FormControl>
                    <Input placeholder="ДПО: Data Science…" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="code"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Код</FormLabel>
                  <FormControl>
                    <Input placeholder="dpo-ds…" autoComplete="off" spellCheck={false} {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="product_type"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Тип</FormLabel>
                  <Select value={field.value} onValueChange={field.onChange}>
                    <FormControl>
                      <SelectTrigger className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {PRODUCT_TYPE_OPTIONS.map((option) => (
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
              name="description"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Описание</FormLabel>
                  <FormControl>
                    <Textarea rows={3} {...field} />
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

export function ProductsPage(): ReactNode {
  const navigate = useNavigate();
  const { hasRole } = useAuth();
  const queryClient = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const canToggle = hasRole('admin', 'head_kam');

  const toggleMutation = useMutation({
    mutationFn: ({ product, isActive }: { product: Product; isActive: boolean }) =>
      updateProduct(product.id, { is_active: isActive, version: product.version }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['registry', 'registry.products'] });
      void queryClient.invalidateQueries({ queryKey: ['products-select'] });
    },
    onError: (error) => toastError(error, { title: 'Не удалось изменить активность' }),
  });

  const columns: DataTableColumn<Product>[] = [
    {
      key: 'name',
      title: 'Название',
      dataIndex: 'name',
      sorter: true,
      alwaysVisible: true,
      render: (_v, record) => (
        <Link
          to={`/registry/products/${record.id}`}
          className="font-medium text-primary hover:underline"
        >
          {record.name}
        </Link>
      ),
    },
    {
      key: 'code',
      title: 'Код',
      dataIndex: 'code',
      render: (value) => <Badge variant="secondary">{value as Product['code']}</Badge>,
    },
    {
      key: 'product_type',
      title: 'Тип',
      dataIndex: 'product_type',
      render: (value) =>
        value ? (PRODUCT_TYPE_LABELS[value as string] ?? (value as string)) : formatText(null),
    },
    {
      key: 'is_active',
      title: 'Активен',
      dataIndex: 'is_active',
      render: (value, record) =>
        canToggle ? (
          <Switch
            checked={Boolean(value)}
            aria-label={`Активность продукта ${record.name}`}
            disabled={toggleMutation.isPending && toggleMutation.variables?.product.id === record.id}
            onCheckedChange={(checked) => toggleMutation.mutate({ product: record, isActive: checked })}
            onClick={(e) => e.stopPropagation()}
          />
        ) : value ? (
          <StatusBadge status="success">Да</StatusBadge>
        ) : (
          <StatusBadge status="draft">Нет</StatusBadge>
        ),
    },
    {
      key: 'updated_at',
      title: 'Обновлён',
      dataIndex: 'updated_at',
      sorter: true,
      responsive: ['lg'],
      render: (value) => formatDate(value as Product['updated_at']),
    },
  ];

  return (
    <>
      <PageHeader
        title="Продукты"
        extra={
          <Can roles={['admin', 'head_kam']}>
            <Button onClick={() => setCreateOpen(true)}>
              <Plus aria-hidden="true" />
              Создать продукт
            </Button>
          </Can>
        }
      />
      <DataTable<Product>
        screen="registry.products"
        columns={columns}
        rowKey="id"
        fetcher={listProducts}
        searchPlaceholder="Название или код"
        defaultSort={{ field: 'name', order: 'asc' }}
        renderFilters={({ filters, setFilter }) => (
          <FilterSelect
            placeholder="Активность"
            allLabel="Активность: все"
            options={[
              { value: 'true', label: 'Активные' },
              { value: 'false', label: 'Неактивные' },
            ]}
            value={filters.is_active as string | undefined}
            onChange={(value) => setFilter('is_active', value)}
          />
        )}
        emptyIllustration="registry-empty"
        emptyTitle="Продуктов пока нет"
        emptyDescription="Продукты создаёт руководитель КАМов или администратор"
        onRowClick={(record) => navigate(`/registry/products/${record.id}`)}
      />
      <CreateProductSheet open={createOpen} onClose={() => setCreateOpen(false)} />
    </>
  );
}
