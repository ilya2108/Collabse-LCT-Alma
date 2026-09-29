import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Loader2, ShieldCheck } from 'lucide-react';
import { useEffect, type ReactNode } from 'react';
import { useForm } from 'react-hook-form';
import { z } from 'zod';
import { createStudent, type StudentPayload } from '@/shared/api/endpoints/students';
import { isApiError } from '@/shared/api/errors';
import type { StudentFunnelStatus } from '@/shared/api/types';
import { toastError, toastSuccess } from '@/shared/lib/toast';
import { Button } from '@/shared/ui/button';
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
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/shared/ui/sheet';
import { Textarea } from '@/shared/ui/textarea';
import { ProgramCombobox, UniversityCombobox } from './EntityCombobox';
import { StatusBadge } from './local';
import { FUNNEL_META, FUNNEL_ORDER } from './model';

/**
 * Sheet-форма «Добавить студента» (redesign.md §5.1: Drawer → Sheet 480px,
 * форма — §5.2 RHF+zod). ФИО и email шифруются на backend — у полей бейдж
 * «ПДн», без длинных абзацев.
 */

interface StudentFormDrawerProps {
  open: boolean;
  onClose: () => void;
}

const schema = z.object({
  full_name: z.string().trim().min(1, 'Укажите ФИО студента').max(200),
  email: z.string().trim().min(1, 'Укажите email').email('Некорректный email').max(200),
  phone: z.string().trim().max(30).optional(),
  university_id: z.string().nullable().optional(),
  program_id: z.string().nullable().optional(),
  funnel_status: z.enum(['candidate', 'studying', 'graduate', 'talent_pool']),
  notes: z.string().trim().max(1000).optional(),
});

type FormValues = z.infer<typeof schema>;

const DEFAULTS: FormValues = {
  full_name: '',
  email: '',
  phone: '',
  university_id: null,
  program_id: null,
  funnel_status: 'candidate',
  notes: '',
};

function PiiLabel({ children }: { children: string }): ReactNode {
  return (
    <span className="flex items-center gap-2">
      {children}
      <StatusBadge status="talent" icon={ShieldCheck} size="sm">
        ПДн: будет зашифровано
      </StatusBadge>
    </span>
  );
}

export function StudentFormDrawer({ open, onClose }: StudentFormDrawerProps): ReactNode {
  const queryClient = useQueryClient();
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    mode: 'onTouched',
    defaultValues: DEFAULTS,
  });

  useEffect(() => {
    if (open) form.reset(DEFAULTS);
  }, [open, form]);

  const mutation = useMutation({
    mutationFn: (values: FormValues) => {
      const payload: StudentPayload = {
        full_name: values.full_name.trim(),
        email: values.email.trim(),
        phone: values.phone?.trim() || null,
        university_id: values.university_id ?? null,
        program_id: values.program_id ?? null,
        funnel_status: values.funnel_status,
        notes: values.notes?.trim() || null,
      };
      return createStudent(payload);
    },
    meta: { silent: true },
    onSuccess: (student) => {
      toastSuccess('Студент добавлен', student.display_name);
      void queryClient.invalidateQueries({ queryKey: ['registry', 'talent-pool'] });
      void queryClient.invalidateQueries({ queryKey: ['students-board'] });
      void queryClient.invalidateQueries({ queryKey: ['talent-pool-funnel'] });
      onClose();
    },
    onError: (error) => {
      if (isApiError(error) && error.status === 409) {
        toastError(error, { title: 'Студент с таким email уже существует' });
        return;
      }
      toastError(error, { title: 'Не удалось добавить студента' });
    },
  });

  const submit = form.handleSubmit((values) => mutation.mutate(values));

  return (
    <Sheet open={open} onOpenChange={(next) => (!next ? onClose() : undefined)}>
      <SheetContent side="right" className="flex w-full flex-col gap-0 sm:max-w-[480px]">
        <SheetHeader>
          <SheetTitle>Добавить студента</SheetTitle>
        </SheetHeader>
        <Form {...form}>
          <form
            className="flex min-h-0 flex-1 flex-col"
            onSubmit={(e) => {
              e.preventDefault();
              void submit();
            }}
          >
            <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4">
              <FormField
                control={form.control}
                name="full_name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>
                      <PiiLabel>ФИО</PiiLabel>
                    </FormLabel>
                    <FormControl>
                      <Input
                        placeholder="Иванов Иван Иванович…"
                        maxLength={200}
                        autoComplete="off"
                        {...field}
                      />
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
                    <FormLabel>
                      <PiiLabel>Email</PiiLabel>
                    </FormLabel>
                    <FormControl>
                      <Input
                        type="email"
                        placeholder="student@example.com"
                        maxLength={200}
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
                      <Input
                        type="tel"
                        inputMode="tel"
                        placeholder="+7 900 000-00-00"
                        maxLength={30}
                        autoComplete="off"
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="university_id"
                render={({ field }) => (
                  <FormItem className="flex flex-col">
                    <FormLabel>Вуз</FormLabel>
                    <UniversityCombobox
                      value={field.value}
                      onChange={field.onChange}
                      className="w-full"
                    />
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="program_id"
                render={({ field }) => (
                  <FormItem className="flex flex-col">
                    <FormLabel>Программа</FormLabel>
                    <ProgramCombobox
                      value={field.value}
                      onChange={field.onChange}
                      className="w-full"
                    />
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="funnel_status"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Статус воронки</FormLabel>
                    <Select
                      value={field.value}
                      onValueChange={(value) => field.onChange(value as StudentFunnelStatus)}
                    >
                      <FormControl>
                        <SelectTrigger className="w-full">
                          <SelectValue />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {FUNNEL_ORDER.map((status) => (
                          <SelectItem key={status} value={status}>
                            {FUNNEL_META[status].short}
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
                name="notes"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Примечание</FormLabel>
                    <FormControl>
                      <Textarea rows={3} maxLength={1000} {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>
            <SheetFooter className="flex-row justify-end gap-2 border-t">
              <Button type="button" variant="outline" onClick={onClose}>
                Отмена
              </Button>
              <Button type="submit" disabled={mutation.isPending} aria-live="polite">
                {mutation.isPending ? (
                  <>
                    <Loader2 className="animate-spin" aria-hidden="true" />
                    Сохраняем…
                  </>
                ) : (
                  'Добавить'
                )}
              </Button>
            </SheetFooter>
          </form>
        </Form>
      </SheetContent>
    </Sheet>
  );
}
