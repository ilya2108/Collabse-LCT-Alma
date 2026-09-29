import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  FileText,
  GraduationCap,
  Loader2,
  Pencil,
  Plus,
  ScrollText,
  Trash2,
} from 'lucide-react';
import { useMemo, useState, type ReactNode } from 'react';
import { useForm } from 'react-hook-form';
import { Link, useParams } from 'react-router-dom';
import { z } from 'zod';
import { listContractsByUniversity } from '@/shared/api/endpoints/contracts';
import { listActiveProducts } from '@/shared/api/endpoints/products';
import { listRequests } from '@/shared/api/endpoints/requests';
import {
  createUniversityContact,
  deleteUniversityContact,
  getUniversity,
  listUniversityContacts,
  updateUniversity,
  updateUniversityContact,
  type UniversityContactPayload,
  type UniversityPayload,
} from '@/shared/api/endpoints/universities';
import type { University, UniversityContact } from '@/shared/api/types';
import { Can } from '@/shared/auth/Can';
import { useAuth } from '@/shared/auth/AuthContext';
import { formatDate, formatMoney, formatText } from '@/shared/lib/format';
import { toastError, toastSuccess } from '@/shared/lib/toast';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/shared/ui/alert-dialog';
import { Badge } from '@/shared/ui/badge';
import { Button } from '@/shared/ui/button';
import {
  ContractStatusTag,
  DL,
  DLRow,
  ErrorState,
  LoadingState,
  MiniCard,
  PageHeader,
  SectionCard,
  StageTag,
  StaggerGrid,
  StaggerItem,
  StatusBadge,
  UserCombobox,
} from '@/shared/ui/data-table';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/ui/dialog';
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/ui/table';
import { Textarea } from '@/shared/ui/textarea';

/**
 * Карточка вуза (ux.md §9.1, redesign.md §6.5): PageHeader с backTo и meta,
 * реквизиты — DL-сетка вместо Descriptions, справа — договор, активные
 * заявки и продукты; карточки-обзоры входят stagger'ом (§2.3).
 */

// --- Контактные лица ----------------------------------------------------------

const contactSchema = z.object({
  full_name: z.string().trim().min(1, 'Укажите ФИО'),
  position: z.string().optional(),
  email: z.string().email('Некорректный email').optional().or(z.literal('')),
  phone: z.string().optional(),
});

type ContactFormValues = z.infer<typeof contactSchema>;

function ContactsCard({ universityId }: { universityId: string }): ReactNode {
  const queryClient = useQueryClient();
  const { hasRole } = useAuth();
  const [editing, setEditing] = useState<UniversityContact | 'new' | null>(null);

  const form = useForm<ContactFormValues>({
    resolver: zodResolver(contactSchema),
    mode: 'onTouched',
    defaultValues: { full_name: '', position: '', email: '', phone: '' },
  });

  const contactsQuery = useQuery({
    queryKey: ['university-contacts', universityId],
    queryFn: ({ signal }) => listUniversityContacts(universityId, signal),
  });

  const invalidate = (): void => {
    void queryClient.invalidateQueries({ queryKey: ['university-contacts', universityId] });
  };

  const saveMutation = useMutation({
    mutationFn: (payload: UniversityContactPayload) =>
      editing && editing !== 'new'
        ? updateUniversityContact(universityId, editing.id, payload)
        : createUniversityContact(universityId, payload),
    onSuccess: () => {
      toastSuccess('Контакт сохранён');
      invalidate();
      setEditing(null);
    },
    onError: (error) => toastError(error, { title: 'Не удалось сохранить контакт' }),
  });
  const deleteMutation = useMutation({
    mutationFn: (contactId: string) => deleteUniversityContact(universityId, contactId),
    onSuccess: () => {
      toastSuccess('Контакт удалён');
      invalidate();
    },
    onError: (error) => toastError(error, { title: 'Не удалось удалить контакт' }),
  });

  const openForm = (contact: UniversityContact | 'new'): void => {
    setEditing(contact);
    if (contact === 'new') form.reset({ full_name: '', position: '', email: '', phone: '' });
    else
      form.reset({
        full_name: contact.full_name,
        position: contact.position ?? '',
        email: contact.email ?? '',
        phone: contact.phone ?? '',
      });
  };

  const contacts = contactsQuery.data?.items ?? [];

  return (
    <SectionCard
      title="Контактные лица"
      extra={
        <Can permission="universities:write">
          <Button variant="outline" size="sm" onClick={() => openForm('new')}>
            <Plus aria-hidden="true" />
            Добавить
          </Button>
        </Can>
      }
    >
      {contactsQuery.isLoading ? (
        <LoadingState rows={3} card={false} />
      ) : contacts.length === 0 ? (
        <p className="py-2 text-sm text-muted-foreground">Контакты не добавлены</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead>ФИО</TableHead>
              <TableHead>Должность</TableHead>
              <TableHead>Email</TableHead>
              <TableHead className="max-lg:hidden">Телефон</TableHead>
              <TableHead className="w-[80px]"> </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {contacts.map((contact) => (
              <TableRow key={contact.id}>
                <TableCell className="font-medium">{contact.full_name}</TableCell>
                <TableCell>{formatText(contact.position)}</TableCell>
                <TableCell>
                  {contact.email ? (
                    <a href={`mailto:${contact.email}`} className="text-primary hover:underline">
                      {contact.email}
                    </a>
                  ) : (
                    '—'
                  )}
                </TableCell>
                <TableCell className="tabular max-lg:hidden">{formatText(contact.phone)}</TableCell>
                <TableCell>
                  <span className="flex items-center">
                    <Can permission="universities:write">
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-7"
                        aria-label={`Изменить контакт ${contact.full_name}`}
                        onClick={() => openForm(contact)}
                      >
                        <Pencil aria-hidden="true" />
                      </Button>
                    </Can>
                    {hasRole('admin', 'head_kam') ? (
                      <AlertDialog>
                        <AlertDialogTrigger asChild>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="size-7 text-status-danger-deep hover:bg-status-danger-tint"
                            aria-label={`Удалить контакт ${contact.full_name}`}
                          >
                            <Trash2 aria-hidden="true" />
                          </Button>
                        </AlertDialogTrigger>
                        <AlertDialogContent>
                          <AlertDialogHeader>
                            <AlertDialogTitle>Удалить контакт?</AlertDialogTitle>
                            <AlertDialogDescription>
                              {contact.full_name} — контакт исчезнет из карточки вуза
                            </AlertDialogDescription>
                          </AlertDialogHeader>
                          <AlertDialogFooter>
                            <AlertDialogCancel>Отмена</AlertDialogCancel>
                            <AlertDialogAction
                              className="bg-destructive text-destructive-foreground hover:bg-status-danger-deep"
                              onClick={() => deleteMutation.mutate(contact.id)}
                            >
                              Удалить
                            </AlertDialogAction>
                          </AlertDialogFooter>
                        </AlertDialogContent>
                      </AlertDialog>
                    ) : null}
                  </span>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <Dialog open={editing !== null} onOpenChange={(open) => (open ? undefined : setEditing(null))}>
        <DialogContent className="sm:max-w-[420px]">
          <DialogHeader>
            <DialogTitle>{editing === 'new' ? 'Новый контакт' : 'Контактное лицо'}</DialogTitle>
          </DialogHeader>
          <Form {...form}>
            <form
              className="flex flex-col gap-4"
              onSubmit={form.handleSubmit((values) =>
                saveMutation.mutate({
                  full_name: values.full_name.trim(),
                  position: values.position?.trim() || null,
                  email: values.email?.trim() || null,
                  phone: values.phone?.trim() || null,
                }),
              )}
            >
              <FormField
                control={form.control}
                name="full_name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>ФИО</FormLabel>
                    <FormControl>
                      <Input placeholder="Иванов Иван Иванович…" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="position"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Должность</FormLabel>
                    <FormControl>
                      <Input placeholder="Проректор по развитию…" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="email"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Email</FormLabel>
                    <FormControl>
                      <Input
                        type="email"
                        placeholder="ivanov@university.ru…"
                        autoComplete="off"
                        spellCheck={false}
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="phone"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Телефон</FormLabel>
                    <FormControl>
                      <Input type="tel" placeholder="+7 495 000-00-00…" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => setEditing(null)}>
                  Отмена
                </Button>
                <Button type="submit" disabled={saveMutation.isPending}>
                  {saveMutation.isPending ? <Loader2 className="animate-spin" aria-hidden="true" /> : null}
                  Сохранить
                </Button>
              </DialogFooter>
            </form>
          </Form>
        </DialogContent>
      </Dialog>
    </SectionCard>
  );
}

// --- Редактирование вуза ------------------------------------------------------

const universityEditSchema = z.object({
  name: z.string().trim().min(1, 'Укажите название'),
  short_name: z.string().optional(),
  inn: z.string().optional(),
  kpp: z.string().optional(),
  region: z.string().optional(),
  city: z.string().optional(),
  website: z.string().optional(),
  kam_user_id: z.string().optional(),
  notes: z.string().optional(),
});

type UniversityEditValues = z.infer<typeof universityEditSchema>;

function EditUniversitySheet({
  university,
  open,
  onClose,
}: {
  university: University;
  open: boolean;
  onClose: () => void;
}): ReactNode {
  const queryClient = useQueryClient();
  const { hasRole } = useAuth();
  const form = useForm<UniversityEditValues>({
    resolver: zodResolver(universityEditSchema),
    mode: 'onTouched',
    values: {
      name: university.name,
      short_name: university.short_name ?? '',
      inn: university.inn ?? '',
      kpp: university.kpp ?? '',
      region: university.region ?? '',
      city: university.city ?? '',
      website: university.website ?? '',
      kam_user_id: university.kam_user_id ?? undefined,
      notes: university.notes ?? '',
    },
  });
  const mutation = useMutation({
    mutationFn: (patch: Partial<UniversityPayload> & { version: number }) =>
      updateUniversity(university.id, patch),
    onSuccess: () => {
      toastSuccess('Карточка вуза сохранена');
      void queryClient.invalidateQueries({ queryKey: ['university', university.id] });
      void queryClient.invalidateQueries({ queryKey: ['registry', 'registry.universities'] });
      onClose();
    },
    onError: (error) => toastError(error, { title: 'Не удалось сохранить вуз' }),
  });

  const textField = (
    name: keyof UniversityEditValues,
    label: string,
    placeholder?: string,
  ): ReactNode => (
    <FormField
      control={form.control}
      name={name}
      render={({ field }) => (
        <FormItem>
          <FormLabel>{label}</FormLabel>
          <FormControl>
            <Input placeholder={placeholder} {...field} />
          </FormControl>
          <FormMessage />
        </FormItem>
      )}
    />
  );

  return (
    <Sheet open={open} onOpenChange={(next) => (next ? undefined : onClose())}>
      <SheetContent side="right" className="sm:max-w-[480px]">
        <SheetHeader>
          <SheetTitle>{university.name}</SheetTitle>
          <SheetDescription>Реквизиты и ответственный КАМ</SheetDescription>
        </SheetHeader>
        <Form {...form}>
          <form
            className="flex flex-col gap-4 overflow-y-auto px-4 pb-4"
            onSubmit={form.handleSubmit((values) =>
              mutation.mutate({
                version: university.version,
                name: values.name.trim(),
                short_name: values.short_name?.trim() || null,
                inn: values.inn?.trim() || null,
                kpp: values.kpp?.trim() || null,
                region: values.region?.trim() || null,
                city: values.city?.trim() || null,
                website: values.website?.trim() || null,
                kam_user_id: values.kam_user_id ?? null,
                notes: values.notes?.trim() || null,
              }),
            )}
          >
            {textField('name', 'Название')}
            {textField('short_name', 'Краткое название')}
            <div className="grid grid-cols-2 gap-3">
              {textField('inn', 'ИНН')}
              {textField('kpp', 'КПП')}
            </div>
            <div className="grid grid-cols-2 gap-3">
              {textField('region', 'Регион')}
              {textField('city', 'Город')}
            </div>
            {textField('website', 'Сайт', 'https://…')}
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

// --- Страница ------------------------------------------------------------------

export function UniversityDetailPage(): ReactNode {
  const { id = '' } = useParams<'id'>();
  const { hasPermission } = useAuth();
  const [editOpen, setEditOpen] = useState(false);

  const universityQuery = useQuery({
    queryKey: ['university', id],
    queryFn: ({ signal }) => getUniversity(id, signal),
    enabled: Boolean(id),
  });
  const contractsQuery = useQuery({
    queryKey: ['university-contracts', id],
    queryFn: ({ signal }) => listContractsByUniversity(id, signal),
    enabled: Boolean(id),
  });
  const requestsQuery = useQuery({
    queryKey: ['university-requests', id],
    queryFn: ({ signal }) => listRequests({ university_id: id }, { limit: 100, signal }),
    enabled: Boolean(id),
  });
  const productsQuery = useQuery({
    queryKey: ['products-select'],
    queryFn: ({ signal }) => listActiveProducts(signal),
    staleTime: 60_000,
  });

  const contracts = contractsQuery.data?.items ?? [];
  const productNames = useMemo(() => {
    const map = new Map<string, string>();
    for (const product of productsQuery.data?.items ?? []) map.set(product.id, product.name);
    return map;
  }, [productsQuery.data]);
  /** Продукты вуза — объединение продуктов его договоров (ux.md §9.1: теги). */
  const universityProductIds = useMemo(
    () => [...new Set(contracts.flatMap((contract) => contract.product_ids))],
    [contracts],
  );

  if (universityQuery.isLoading) return <LoadingState rows={10} />;
  if (universityQuery.isError || !universityQuery.data) {
    return (
      <ErrorState
        error={universityQuery.error}
        title="Не удалось загрузить вуз"
        onRetry={() => void universityQuery.refetch()}
      />
    );
  }
  const university = universityQuery.data;
  const universityRequests = requestsQuery.data?.items ?? [];

  return (
    <>
      <PageHeader
        title={university.name}
        backTo="/registry/universities"
        meta={
          <>
            {university.region ? <Badge variant="secondary">{university.region}</Badge> : null}
            {university.is_active ? (
              <StatusBadge status="success">Активен</StatusBadge>
            ) : (
              <StatusBadge status="draft">Неактивен</StatusBadge>
            )}
          </>
        }
        extra={
          hasPermission('universities:write') ? (
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
              <DL>
                <DLRow label="Краткое название">{formatText(university.short_name)}</DLRow>
                <DLRow label="ИНН / КПП">
                  {university.inn || university.kpp ? (
                    <span className="tabular">
                      {formatText(university.inn)} / {formatText(university.kpp)}
                    </span>
                  ) : (
                    '—'
                  )}
                </DLRow>
                <DLRow label="Регион, город">
                  {university.region || university.city
                    ? [university.region, university.city].filter(Boolean).join(', ')
                    : '—'}
                </DLRow>
                <DLRow label="Сайт">
                  {university.website ? (
                    <a
                      href={university.website}
                      target="_blank"
                      rel="noreferrer"
                      className="text-primary hover:underline"
                    >
                      {university.website}
                    </a>
                  ) : (
                    '—'
                  )}
                </DLRow>
                <DLRow label="Примечание">{formatText(university.notes)}</DLRow>
                <DLRow label="Обновлён">{formatDate(university.updated_at)}</DLRow>
              </DL>
            </SectionCard>
          </StaggerItem>
          <StaggerItem>
            <ContactsCard universityId={university.id} />
          </StaggerItem>
        </div>
        <div className="col-span-12 flex flex-col gap-4 lg:col-span-5">
          <StaggerItem>
            <SectionCard
              title={contracts.length > 1 ? `Договоры (${contracts.length})` : 'Договор'}
              extra={
                <Link to="/registry/contracts" className="text-sm text-primary hover:underline">
                  Все договоры
                </Link>
              }
            >
              {contractsQuery.isLoading ? (
                <LoadingState rows={2} card={false} />
              ) : contracts.length === 0 ? (
                <p className="py-2 text-sm text-muted-foreground">Договоров с вузом пока нет</p>
              ) : (
                <div className="flex flex-col gap-2">
                  {contracts.map((contract) => (
                    <MiniCard
                      key={contract.id}
                      to={`/registry/contracts/${contract.id}`}
                      icon={ScrollText}
                      title={contract.number}
                      description={
                        <>
                          {contract.valid_from || contract.valid_to
                            ? `${formatDate(contract.valid_from)} — ${formatDate(contract.valid_to)}`
                            : 'срок не указан'}
                          {contract.amount ? ` · ${formatMoney(contract.amount, contract.currency)}` : ''}
                        </>
                      }
                      extra={<ContractStatusTag status={contract.status} size="sm" />}
                    />
                  ))}
                </div>
              )}
              {contracts.filter((c) => c.status === 'active').length > 1 ? (
                <p className="mt-2 text-xs text-status-warning-deep">
                  У вуза больше одного действующего договора — как правило, договор один.
                </p>
              ) : null}
            </SectionCard>
          </StaggerItem>
          <StaggerItem>
            <SectionCard
              title={`Активные заявки${requestsQuery.data ? ` (${requestsQuery.data.total})` : ''}`}
            >
              {requestsQuery.isLoading ? (
                <LoadingState rows={3} card={false} />
              ) : universityRequests.length === 0 ? (
                <p className="py-2 text-sm text-muted-foreground">Заявок по вузу нет</p>
              ) : (
                <div className="flex flex-col gap-2">
                  {universityRequests.map((request) => (
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
          <StaggerItem>
            <SectionCard title="Продукты и студенты">
              <div className="flex flex-col gap-3">
                {universityProductIds.length > 0 ? (
                  <div className="flex flex-wrap gap-1.5">
                    {universityProductIds.map((productId) => (
                      <Badge key={productId} variant="secondary">
                        {productNames.get(productId) ?? '…'}
                      </Badge>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">Продукты не подключены</p>
                )}
                <Link
                  to={`/talent-pool?university=${university.id}`}
                  className="inline-flex items-center gap-1.5 text-sm text-primary hover:underline"
                >
                  <GraduationCap className="size-4" aria-hidden="true" />
                  Студенты вуза
                  {typeof university.students_count === 'number'
                    ? ` (${university.students_count})`
                    : ''}
                </Link>
              </div>
            </SectionCard>
          </StaggerItem>
        </div>
      </StaggerGrid>
      <EditUniversitySheet
        university={university}
        open={editOpen}
        onClose={() => setEditOpen(false)}
      />
    </>
  );
}
