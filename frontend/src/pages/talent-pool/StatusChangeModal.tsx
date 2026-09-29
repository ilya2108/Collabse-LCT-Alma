import { zodResolver } from '@hookform/resolvers/zod';
import { ArrowRight, Loader2, TriangleAlert } from 'lucide-react';
import { useEffect, type ReactNode } from 'react';
import { useForm } from 'react-hook-form';
import { z } from 'zod';
import type { Student, StudentFunnelStatus } from '@/shared/api/types';
import { Alert, AlertTitle } from '@/shared/ui/alert';
import { Button } from '@/shared/ui/button';
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
import { Textarea } from '@/shared/ui/textarea';
import { StatusBadge } from './local';
import { FUNNEL_META, funnelMeta, isFunnelReturn } from './model';

/**
 * Модалка перевода студента по воронке (ux.md §12.1, форма — §5.2):
 * возврат назад — с обязательной причиной (contract §8.2), вперёд —
 * комментарий опционален.
 */

interface StatusChangeModalProps {
  student: Student | null;
  to: StudentFunnelStatus | null;
  submitting: boolean;
  onSubmit: (comment: string | undefined) => void;
  onCancel: () => void;
}

const makeSchema = (isReturn: boolean) =>
  z.object({
    comment: isReturn
      ? z.string().trim().min(1, 'Укажите причину возврата').max(500)
      : z.string().trim().max(500).optional(),
  });

type FormValues = z.infer<ReturnType<typeof makeSchema>>;

export function StatusChangeModal({
  student,
  to,
  submitting,
  onSubmit,
  onCancel,
}: StatusChangeModalProps): ReactNode {
  const open = Boolean(student && to);
  const isReturn = student && to ? isFunnelReturn(student.funnel_status, to) : false;

  const form = useForm<FormValues>({
    resolver: zodResolver(makeSchema(isReturn)),
    mode: 'onTouched',
    defaultValues: { comment: '' },
  });

  useEffect(() => {
    if (open) form.reset({ comment: '' });
  }, [open, form]);

  if (!student || !to) return null;

  const submit = form.handleSubmit((values) => {
    onSubmit(values.comment?.trim() || undefined);
  });

  return (
    <Dialog open={open} onOpenChange={(next) => (!next ? onCancel() : undefined)}>
      <DialogContent className="sm:max-w-[440px]">
        <DialogHeader>
          <DialogTitle>Перевод по воронке</DialogTitle>
        </DialogHeader>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="min-w-0 truncate font-medium">{student.display_name}</span>
          <StatusBadge color={funnelMeta(student.funnel_status).color}>
            {funnelMeta(student.funnel_status).short}
          </StatusBadge>
          <ArrowRight className="size-4 text-muted-foreground" aria-hidden="true" />
          <StatusBadge color={FUNNEL_META[to].color}>{FUNNEL_META[to].short}</StatusBadge>
        </div>
        {isReturn ? (
          <Alert className="border-status-warning bg-status-warning-tint text-status-warning-deep">
            <TriangleAlert className="size-4" aria-hidden="true" />
            <AlertTitle className="text-status-warning-deep">
              Возврат назад по воронке — причина попадёт в историю студента
            </AlertTitle>
          </Alert>
        ) : null}
        <Form {...form}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void submit();
            }}
            className="space-y-4"
          >
            <FormField
              control={form.control}
              name="comment"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{isReturn ? 'Причина возврата' : 'Комментарий'}</FormLabel>
                  <FormControl>
                    <Textarea
                      rows={3}
                      maxLength={500}
                      placeholder={
                        isReturn ? 'Например: отчислен по собственному желанию…' : 'Необязательно'
                      }
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <DialogFooter>
              <Button type="button" variant="outline" onClick={onCancel}>
                Отмена
              </Button>
              <Button
                type="submit"
                variant={isReturn ? 'destructive' : 'default'}
                disabled={submitting}
              >
                {submitting ? <Loader2 className="animate-spin" aria-hidden="true" /> : null}
                {isReturn ? 'Вернуть' : 'Перевести'}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
