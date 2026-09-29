import { zodResolver } from '@hookform/resolvers/zod';
import { Loader2 } from 'lucide-react';
import { useForm } from 'react-hook-form';
import { z } from 'zod';
import type { ReactNode } from 'react';
import { Button } from '@/shared/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/ui/dialog';
import { Form, FormControl, FormField, FormItem, FormMessage } from '@/shared/ui/form';
import { Textarea } from '@/shared/ui/textarea';

/**
 * Диалог обязательного комментария перехода (ux.md §7.2, redesign.md §5.2):
 * показывается для возвратов (`kind: return`) и транзиций с
 * `requires_comment: true`. Без комментария сервер вернёт 400 — не доводим:
 * zod-схема + inline-ошибка под полем.
 */

interface TransitionCommentModalProps {
  open: boolean;
  /** Название перехода («Вернуть в переговоры») и целевой этап. */
  actionName: string;
  toStatusName: string;
  isReturn: boolean;
  loading: boolean;
  onConfirm: (comment: string) => void;
  onCancel: () => void;
}

const schema = z.object({
  comment: z
    .string()
    .trim()
    .min(1, 'Укажите причину — без неё переход невозможен')
    .max(1000, 'Не длиннее 1000 символов'),
});

type FormValues = z.infer<typeof schema>;

export function TransitionCommentModal({
  open,
  actionName,
  toStatusName,
  isReturn,
  loading,
  onConfirm,
  onCancel,
}: TransitionCommentModalProps): ReactNode {
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    mode: 'onTouched',
    defaultValues: { comment: '' },
  });

  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next && !loading) onCancel(); }}>
      <DialogContent className="sm:max-w-[440px]">
        <DialogHeader>
          <DialogTitle>{isReturn ? 'Причина возврата' : 'Комментарий к переходу'}</DialogTitle>
          <DialogDescription>
            {actionName} → этап «{toStatusName}». Комментарий обязателен и попадёт в историю заявки.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form
            onSubmit={form.handleSubmit((values) => onConfirm(values.comment))}
            className="grid gap-4"
          >
            <FormField
              control={form.control}
              name="comment"
              render={({ field }) => (
                <FormItem>
                  <FormControl>
                    <Textarea
                      {...field}
                      rows={3}
                      autoFocus
                      maxLength={1000}
                      placeholder={
                        isReturn ? 'Почему возвращаем на предыдущий этап…' : 'Комментарий к переходу…'
                      }
                      aria-label={isReturn ? 'Причина возврата' : 'Комментарий к переходу'}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <DialogFooter>
              <Button type="button" variant="outline" disabled={loading} onClick={onCancel}>
                Отмена
              </Button>
              <Button
                type="submit"
                variant={isReturn ? 'destructive' : 'default'}
                disabled={loading}
                aria-live="polite"
              >
                {loading ? <Loader2 className="animate-spin" aria-hidden="true" /> : null}
                {loading ? 'Сохраняем…' : isReturn ? 'Вернуть' : 'Перевести'}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
