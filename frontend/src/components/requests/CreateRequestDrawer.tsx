import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';
import { useEffect, useMemo, type ReactNode } from 'react';
import { useForm, type Path } from 'react-hook-form';
import { z } from 'zod';
import {
  ContractSelect,
  InteractionTypeSelect,
  ProductSelect,
  ProgramSelect,
  UniversitySelect,
  UserSelect,
} from '@/components/selects/EntitySelects';
import { isApiError } from '@/shared/api/errors';
import { createRequest, type CreateRequestPayload } from '@/shared/api/endpoints/requests';
import type { RequestItem, WorkflowType } from '@/shared/api/types';
import { useAuth } from '@/shared/auth/AuthContext';
import { toastError, toastSuccess } from '@/shared/lib/toast';
import { Button } from '@/shared/ui/button';
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/shared/ui/form';
import { Input } from '@/shared/ui/input';
import { RadioGroup, RadioGroupItem } from '@/shared/ui/radio-group';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/shared/ui/sheet';
import { Textarea } from '@/shared/ui/textarea';

/**
 * Создание заявки (ux.md §7.4, redesign.md §5.2): Sheet 480px, react-hook-form
 * + zod. Заявка всегда создаётся в начальном этапе схемы; B2B — вуз
 * обязателен, B2C — контрагент (физлицо/юрлицо) + тип взаимодействия.
 * Ошибки 422 маппятся в поля через form.setError.
 */

interface CreateRequestDrawerProps {
  workflowType: WorkflowType;
  open: boolean;
  onClose: () => void;
  onCreated?: (request: RequestItem) => void;
}

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const INN_RE = /^\d{10}$|^\d{12}$/;

function buildSchema(workflowType: WorkflowType) {
  return z
    .object({
      title: z.string().trim().min(1, 'Укажите название'),
      university_id: z.string().optional(),
      contract_id: z.string().optional(),
      client_kind: z.enum(['person', 'company']),
      interaction_type_id: z.string().optional(),
      client_full_name: z.string().optional(),
      client_company_name: z.string().optional(),
      client_inn: z.string().optional(),
      client_email: z.string().optional(),
      client_phone: z.string().optional(),
      product_id: z.string().optional(),
      program_id: z.string().optional(),
      assignee_id: z.string().optional(),
      amount: z.string().optional(),
      description: z.string().optional(),
    })
    .superRefine((values, ctx) => {
      if (workflowType === 'b2b' && !values.university_id) {
        ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['university_id'], message: 'Для B2B-заявки вуз обязателен' });
      }
      if (workflowType === 'b2c') {
        if (!values.interaction_type_id) {
          ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['interaction_type_id'], message: 'Выберите тип взаимодействия' });
        }
        if (values.client_kind === 'person' && !values.client_full_name?.trim()) {
          ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['client_full_name'], message: 'Укажите ФИО клиента' });
        }
        if (values.client_kind === 'company' && !values.client_company_name?.trim()) {
          ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['client_company_name'], message: 'Укажите организацию' });
        }
        if (values.client_inn?.trim() && !INN_RE.test(values.client_inn.trim())) {
          ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['client_inn'], message: 'ИНН — 10 или 12 цифр' });
        }
        if (values.client_email?.trim() && !EMAIL_RE.test(values.client_email.trim())) {
          ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['client_email'], message: 'Некорректный email' });
        }
      }
      if (values.amount?.trim()) {
        const num = Number(values.amount.replace(',', '.'));
        if (!Number.isFinite(num) || num < 0) {
          ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['amount'], message: 'Сумма — неотрицательное число' });
        }
      }
    });
}

type FormValues = z.infer<ReturnType<typeof buildSchema>>;

const DEFAULTS: FormValues = {
  title: '',
  client_kind: 'person',
};

const FORM_FIELDS = [
  'title',
  'university_id',
  'contract_id',
  'client_kind',
  'interaction_type_id',
  'client_full_name',
  'client_company_name',
  'client_inn',
  'client_email',
  'client_phone',
  'product_id',
  'program_id',
  'assignee_id',
  'amount',
  'description',
] as const;

/** 422: `loc/field` бэкенда → имя поля формы (client.full_name → client_full_name). */
function apiFieldToFormField(field: string): Path<FormValues> | null {
  const normalized = field.replace(/^body\./, '').replace('client.', 'client_');
  return (FORM_FIELDS as readonly string[]).includes(normalized)
    ? (normalized as Path<FormValues>)
    : null;
}

export function CreateRequestDrawer({
  workflowType,
  open,
  onClose,
  onCreated,
}: CreateRequestDrawerProps): ReactNode {
  const queryClient = useQueryClient();
  const { hasRole } = useAuth();

  const schema = useMemo(() => buildSchema(workflowType), [workflowType]);
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    mode: 'onTouched',
    defaultValues: DEFAULTS,
  });

  useEffect(() => {
    if (open) form.reset(DEFAULTS);
  }, [open, workflowType, form]);

  const clientKind = form.watch('client_kind');
  const universityId = form.watch('university_id');
  const productId = form.watch('product_id');

  const mutation = useMutation({
    mutationFn: createRequest,
    meta: { silent: true },
    onSuccess: (request) => {
      toastSuccess('Заявка создана', `«${request.title}» — этап «${request.status.name}»`);
      void queryClient.invalidateQueries({ queryKey: ['board-requests'] });
      void queryClient.invalidateQueries({ queryKey: ['registry'] });
      form.reset(DEFAULTS);
      onClose();
      onCreated?.(request);
    },
    onError: (error) => {
      // 422 → inline-ошибки полей (§5.2), непривязанные — toast
      if (isApiError(error) && error.status === 422 && error.details.length > 0) {
        let unbound = false;
        for (const detail of error.details) {
          const field = detail.field ? apiFieldToFormField(detail.field) : null;
          if (field) form.setError(field, { message: detail.message });
          else unbound = true;
        }
        const first = error.details.find((d) => d.field && apiFieldToFormField(d.field));
        if (first?.field) form.setFocus(apiFieldToFormField(first.field) as Path<FormValues>);
        if (unbound) toastError(error, { title: 'Заявка не создана' });
        return;
      }
      toastError(error, { title: 'Заявка не создана' });
    },
  });

  const submit = (values: FormValues): void => {
    const amount = values.amount?.trim()
      ? Number(values.amount.replace(',', '.')).toFixed(2)
      : null;
    const payload: CreateRequestPayload = {
      workflow_type: workflowType,
      title: values.title.trim(),
      product_id: values.product_id ?? null,
      program_id: values.program_id ?? null,
      amount,
      description: values.description?.trim() || null,
    };
    if (values.assignee_id) payload.assignee_id = values.assignee_id;
    if (workflowType === 'b2b') {
      payload.university_id = values.university_id;
      payload.contract_id = values.contract_id ?? null;
    } else {
      payload.client_kind = values.client_kind;
      payload.interaction_type_id = values.interaction_type_id;
      payload.client =
        values.client_kind === 'company'
          ? {
              company_name: values.client_company_name?.trim(),
              inn: values.client_inn?.trim() || undefined,
              email: values.client_email?.trim() || undefined,
              phone: values.client_phone?.trim() || undefined,
            }
          : {
              full_name: values.client_full_name?.trim(),
              email: values.client_email?.trim() || undefined,
              phone: values.client_phone?.trim() || undefined,
            };
    }
    mutation.mutate(payload);
  };

  // AntD-дропдауны рендерим внутри Radix-sheet'а, иначе клики гасятся оверлеем
  const popupInSheet = (trigger: HTMLElement): HTMLElement => trigger.parentElement ?? document.body;

  return (
    <Sheet open={open} onOpenChange={(next) => { if (!next) onClose(); }}>
      <SheetContent side="right" className="w-full gap-0 overflow-y-auto sm:max-w-[480px]" aria-describedby={undefined}>
        <SheetHeader className="px-6 pt-6">
          <SheetTitle>
            {workflowType === 'b2b' ? 'Новая заявка B2B (вуз)' : 'Новая заявка B2C'}
          </SheetTitle>
        </SheetHeader>
        <Form {...form}>
          <form className="grid gap-4 p-6 pt-4" onSubmit={form.handleSubmit(submit)}>
            <FormField
              control={form.control}
              name="title"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Название заявки</FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      autoComplete="off"
                      placeholder="МФТИ — ДПО Data Science, поток весна-2027…"
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            {workflowType === 'b2b' ? (
              <>
                <FormField
                  control={form.control}
                  name="university_id"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Вуз</FormLabel>
                      <UniversitySelect
                        value={field.value}
                        onChange={field.onChange}
                        getPopupContainer={popupInSheet}
                      />
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="contract_id"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Договор</FormLabel>
                      <ContractSelect
                        universityId={universityId ?? null}
                        value={field.value}
                        onChange={field.onChange}
                        getPopupContainer={popupInSheet}
                      />
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </>
            ) : (
              <>
                <FormField
                  control={form.control}
                  name="client_kind"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Контрагент</FormLabel>
                      <FormControl>
                        <RadioGroup
                          className="flex gap-4"
                          value={field.value}
                          onValueChange={field.onChange}
                        >
                          <label className="flex cursor-pointer items-center gap-2 text-sm">
                            <RadioGroupItem value="person" />
                            Физлицо
                          </label>
                          <label className="flex cursor-pointer items-center gap-2 text-sm">
                            <RadioGroupItem value="company" />
                            Юрлицо
                          </label>
                        </RadioGroup>
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                {clientKind === 'person' ? (
                  <FormField
                    control={form.control}
                    name="client_full_name"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>ФИО</FormLabel>
                        <FormControl>
                          <Input
                            {...field}
                            value={field.value ?? ''}
                            autoComplete="off"
                            placeholder="Смирнова Ольга Викторовна…"
                          />
                        </FormControl>
                        <FormDescription>ПДн физлица шифруются при сохранении (152-ФЗ)</FormDescription>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                ) : (
                  <>
                    <FormField
                      control={form.control}
                      name="client_company_name"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Название организации</FormLabel>
                          <FormControl>
                            <Input {...field} value={field.value ?? ''} autoComplete="off" placeholder="ООО «Пример»…" />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name="client_inn"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>ИНН</FormLabel>
                          <FormControl>
                            <Input
                              {...field}
                              value={field.value ?? ''}
                              inputMode="numeric"
                              autoComplete="off"
                              spellCheck={false}
                              placeholder="ИНН, 10 цифр…"
                            />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  </>
                )}
                <FormField
                  control={form.control}
                  name="client_email"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Email</FormLabel>
                      <FormControl>
                        <Input
                          {...field}
                          value={field.value ?? ''}
                          type="email"
                          autoComplete="off"
                          spellCheck={false}
                          placeholder="client@example.com…"
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="client_phone"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Телефон</FormLabel>
                      <FormControl>
                        <Input
                          {...field}
                          value={field.value ?? ''}
                          type="tel"
                          autoComplete="off"
                          placeholder="+7 916 000-00-00…"
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="interaction_type_id"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Тип взаимодействия</FormLabel>
                      <InteractionTypeSelect
                        value={field.value}
                        onChange={field.onChange}
                        getPopupContainer={popupInSheet}
                      />
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </>
            )}

            <FormField
              control={form.control}
              name="product_id"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Продукт</FormLabel>
                  <ProductSelect value={field.value} onChange={field.onChange} getPopupContainer={popupInSheet} />
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="program_id"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Программа</FormLabel>
                  <ProgramSelect
                    productId={productId ?? null}
                    value={field.value}
                    onChange={field.onChange}
                    getPopupContainer={popupInSheet}
                  />
                  <FormMessage />
                </FormItem>
              )}
            />
            {hasRole('admin', 'head_kam') ? (
              <FormField
                control={form.control}
                name="assignee_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Ответственный</FormLabel>
                    <UserSelect
                      placeholder="Ответственный КАМ"
                      value={field.value}
                      onChange={field.onChange}
                      getPopupContainer={popupInSheet}
                    />
                    <FormDescription>Пусто — назначить себя</FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
            ) : null}
            <FormField
              control={form.control}
              name="amount"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Сумма, ₽</FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      value={field.value ?? ''}
                      type="number"
                      inputMode="decimal"
                      min={0}
                      step="0.01"
                      autoComplete="off"
                      placeholder="1 250 000…"
                    />
                  </FormControl>
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
                    <Textarea {...field} value={field.value ?? ''} rows={3} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <div className="flex gap-2 pt-1">
              <Button type="submit" disabled={mutation.isPending} aria-live="polite">
                {mutation.isPending ? <Loader2 className="animate-spin" aria-hidden="true" /> : null}
                {mutation.isPending ? 'Сохраняем…' : 'Создать заявку'}
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
