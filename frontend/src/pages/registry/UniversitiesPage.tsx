import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { FileUp, Loader2, Plus } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { useForm } from 'react-hook-form';
import { Link, useNavigate } from 'react-router-dom';
import { z } from 'zod';
import {
  createUniversity,
  listUniversities,
  type UniversityPayload,
} from '@/shared/api/endpoints/universities';
import type { University } from '@/shared/api/types';
import { Can } from '@/shared/auth/Can';
import { useAuth } from '@/shared/auth/AuthContext';
import { IMPORT_ROLES } from '@/shared/auth/roles';
import { formatDate, formatText } from '@/shared/lib/format';
import { toastSuccess, toastError } from '@/shared/lib/toast';
import { Button } from '@/shared/ui/button';
import {
  DataTable,
  FilterSelect,
  PageHeader,
  StatusBadge,
  UserCombobox,
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
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/shared/ui/sheet';

/**
 * Реестр вузов (ux.md §9.1) на DataTable (redesign.md §6.5): серверная
 * пагинация, сортировка, фильтры и пресеты. Ключи колонок совпадают
 * с полями API, чтобы сортировка уходила на сервер как есть.
 */

const columns: DataTableColumn<University>[] = [
  {
    key: 'name',
    title: 'Название',
    dataIndex: 'name',
    sorter: true,
    alwaysVisible: true,
    render: (_value, record) => (
      <Link
        to={`/registry/universities/${record.id}`}
        className="font-medium text-primary hover:underline"
      >
        {record.name}
      </Link>
    ),
  },
  {
    key: 'region',
    title: 'Регион',
    dataIndex: 'region',
    render: (value) => formatText(value as University['region']),
  },
  {
    key: 'city',
    title: 'Город',
    dataIndex: 'city',
    responsive: ['lg'],
    render: (value) => formatText(value as University['city']),
  },
  {
    key: 'inn',
    title: 'ИНН',
    dataIndex: 'inn',
    render: (value) => <span className="tabular">{formatText(value as University['inn'])}</span>,
  },
  {
    key: 'is_active',
    title: 'Статус',
    dataIndex: 'is_active',
    render: (value) =>
      value ? (
        <StatusBadge status="success">Активен</StatusBadge>
      ) : (
        <StatusBadge status="draft">Неактивен</StatusBadge>
      ),
  },
  {
    key: 'updated_at',
    title: 'Обновлён',
    dataIndex: 'updated_at',
    sorter: true,
    render: (value) => formatDate(value as University['updated_at']),
  },
];

// --- Создание вуза (§5.2: RHF + zod, Sheet 480px) ----------------------------

const universitySchema = z.object({
  name: z.string().trim().min(1, 'Укажите название вуза'),
  short_name: z.string().optional(),
  inn: z
    .string()
    .optional()
    .refine((v) => !v || /^\d{10}$|^\d{12}$/.test(v), 'ИНН — 10 или 12 цифр'),
  region: z.string().optional(),
  city: z.string().optional(),
  kam_user_id: z.string().optional(),
});

type UniversityFormValues = z.infer<typeof universitySchema>;

function CreateUniversitySheet({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}): ReactNode {
  const queryClient = useQueryClient();
  const { user, hasRole } = useAuth();
  const form = useForm<UniversityFormValues>({
    resolver: zodResolver(universitySchema),
    mode: 'onTouched',
    defaultValues: { name: '', short_name: '', inn: '', region: '', city: '' },
  });

  const mutation = useMutation({
    mutationFn: (values: UniversityFormValues) =>
      createUniversity({
        name: values.name.trim(),
        short_name: values.short_name?.trim() || null,
        inn: values.inn?.trim() || null,
        region: values.region?.trim() || null,
        city: values.city?.trim() || null,
        // kam создаёт вуз на себя; выбор КАМа — у head_kam/admin
        kam_user_id: values.kam_user_id ?? user?.id ?? null,
      } as UniversityPayload),
    onSuccess: (university) => {
      toastSuccess(`Вуз «${university.name}» создан`);
      void queryClient.invalidateQueries({ queryKey: ['registry', 'registry.universities'] });
      form.reset();
      onClose();
    },
    onError: (error) => toastError(error, { title: 'Не удалось создать вуз' }),
  });

  return (
    <Sheet open={open} onOpenChange={(next) => (next ? undefined : onClose())}>
      <SheetContent side="right" className="sm:max-w-[480px]">
        <SheetHeader>
          <SheetTitle>Новый вуз</SheetTitle>
          <SheetDescription>Название, регион, ИНН и ответственный КАМ</SheetDescription>
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
                    <Input placeholder="Московский физико-технический институт…" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="short_name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Краткое название</FormLabel>
                  <FormControl>
                    <Input placeholder="МФТИ…" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="inn"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>ИНН</FormLabel>
                  <FormControl>
                    <Input
                      placeholder="ИНН, 10 цифр…"
                      inputMode="numeric"
                      autoComplete="off"
                      spellCheck={false}
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <div className="grid grid-cols-2 gap-3">
              <FormField
                control={form.control}
                name="region"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Регион</FormLabel>
                    <FormControl>
                      <Input placeholder="Московская область…" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="city"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Город</FormLabel>
                    <FormControl>
                      <Input placeholder="Долгопрудный…" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>
            {hasRole('admin', 'head_kam') ? (
              <FormField
                control={form.control}
                name="kam_user_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Ответственный КАМ</FormLabel>
                    <FormControl>
                      <UserCombobox
                        role="kam"
                        placeholder="КАМ вуза"
                        className="w-full"
                        value={field.value}
                        onChange={field.onChange}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            ) : null}
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

export function UniversitiesPage(): ReactNode {
  const navigate = useNavigate();
  const [createOpen, setCreateOpen] = useState(false);
  return (
    <>
      <PageHeader
        title="Вузы"
        extra={
          <Can permission="universities:write">
            <Button onClick={() => setCreateOpen(true)}>
              <Plus aria-hidden="true" />
              Создать вуз
            </Button>
          </Can>
        }
      />
      <DataTable<University>
        screen="registry.universities"
        exportEntityType="universities"
        columns={columns}
        rowKey="id"
        fetcher={listUniversities}
        searchPlaceholder="Название, ИНН или город"
        defaultSort={{ field: 'updated_at', order: 'desc' }}
        renderFilters={({ filters, setFilter }) => (
          <>
            <Input
              placeholder="Регион"
              aria-label="Фильтр по региону"
              className="w-[180px]"
              value={(filters.region as string | undefined) ?? ''}
              onChange={(e) => setFilter('region', e.target.value || undefined)}
            />
            <FilterSelect
              placeholder="Активный договор"
              allLabel="Договор: любой"
              options={[
                { value: 'true', label: 'Есть договор' },
                { value: 'false', label: 'Нет договора' },
              ]}
              value={filters.has_active_contract as string | undefined}
              onChange={(value) => setFilter('has_active_contract', value)}
            />
          </>
        )}
        emptyIllustration="registry-empty"
        emptyTitle="Вузов пока нет"
        emptyDescription="Создайте вуз вручную или импортируйте реестр из Excel"
        emptyAction={
          <>
            <Can permission="universities:write">
              <Button onClick={() => setCreateOpen(true)}>
                <Plus aria-hidden="true" />
                Создать вуз
              </Button>
            </Can>
            <Can roles={IMPORT_ROLES}>
              <Button variant="outline" onClick={() => navigate('/import')}>
                <FileUp aria-hidden="true" />
                Импортировать из Excel
              </Button>
            </Can>
          </>
        }
      />
      <CreateUniversitySheet open={createOpen} onClose={() => setCreateOpen(false)} />
    </>
  );
}
